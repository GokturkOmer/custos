"""ISA-18.2 uyumlu Threshold Engine + Layer 1 ek kurallar.

Analytics sürecinde periyodik olarak çalışan arka plan task'ı.
Aktif tag'lerin son değerlerini threshold tanımlarıyla karşılaştırır,
alarm event'leri oluşturur/günceller.

Debounce: Eşik aşımının belirli süre devam etmesi gerekir.
Hysteresis: Alarm temizleme için ölü bant (set_point ± hysteresis).

P-04 (V11-104): Bakım modu entegrasyonu — alarm yazmadan önce per-instance
ve global bakım kontrol edilir; aktifse ``is_test=true`` flag'i ile yazılır
ve push gönderilmez.

R-06 (V11-304/305): Layer 1 ek kurallar aynı tick içinde değerlendirilir:

- **Rate-of-change** (``rate_of_change_threshold`` tag kolonu): Bir tag'in
  okuma değerleri arasındaki delta'yı dakika başına ölçer; mutlak değer
  eşiği aşarsa alarm. Cooldown 5 dk. Source: ``rate_of_change``.
- **Cross-sensor** (``cross_sensor_rules`` tablosu): İki tag arası
  mantıksal kural (lt/gt/eq/neq/lte/gte). İhlal varsa alarm. Cooldown
  10 dk. Source: ``cross_sensor``.

İki kontrol de mevcut threshold engine tick'inin (5 sn) sonuna ek bir
çağrı olarak çalışır — aynı bakım/global flag mantığı + ortak
``send_push_notifications`` çağrısı kullanılır.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Final

import structlog

from custos.analytics import maintenance_mode
from custos.analytics.push_sender import send_push_notifications
from custos.shared.database import (
    AlarmEvent,
    AuditLogEntry,
    CrossSensorRule,
    DatabaseInterface,
    TagReading,
    TagRecord,
)

logger = structlog.get_logger(logger_name="threshold_engine")


# R-06 cooldown'ları — aynı tag/kural için kısa sürede alarm gürültüsünü
# engellemek için. Threshold engine'in 5 sn tick'i ile uyumsuz değil:
# cooldown alarm yazıldıktan SONRA kayıt edilir, aynı sebepten tekrar
# alarm yazımı bu süreyi geçmek zorundadır.
_RATE_OF_CHANGE_COOLDOWN: Final[timedelta] = timedelta(minutes=5)
_CROSS_SENSOR_COOLDOWN: Final[timedelta] = timedelta(minutes=10)

# Rate-of-change için minimum delta zamanı (saniye). Çok kısa aralıklarda
# (ör. 0.5 sn) sayısal hata büyür; pratikte tag polling'i en hızlı 100 ms,
# en yavaş 10 sn — 1 sn altı önemsiz alıp atlıyoruz.
_RATE_MIN_DT_SECONDS: Final[float] = 1.0


class ThresholdEngine:
    """R-06 Layer-1 ek kuralları: rate-of-change + cross-sensor.

    NOT (review H1 / ADR-001): Kullanıcı-tanımlı alt/üst limit (threshold) alarm
    üretimi Critical loop'a taşındı (``critical/threshold_watcher.py``). Bu motor
    artık YALNIZCA rate-of-change ve cross-sensor Layer-1 kurallarını
    değerlendirir (ikisi de Analytics'te kalır: cross-sensor iki tag'in senkron
    okumasını, rate delta-zaman state'ini gerektirir). Sınıf adı geriye-uyum
    için korunuyor.
    """

    def __init__(
        self,
        db: DatabaseInterface,
        check_interval_seconds: float = 5.0,
    ) -> None:
        self._db = db
        self._check_interval = check_interval_seconds
        self._task: asyncio.Task[None] | None = None
        self._running = False
        # P-04: tag_id → instance_id cache, her cycle başında doldurulur.
        # Threshold'un tag'i bir instance'a binding'li ise per-instance bakım
        # kontrolü yapılabilir; binding yoksa ``None``.
        self._tag_instance_map: dict[str, int | None] = {}
        # R-06: rate-of-change için son değerlendirilen okuma — delta
        # hesabı için bir önceki tick'in son değerini hatırlıyoruz.
        # Tag silinince entry kaybolur (cycle başında bilinmeyen tag yoksa
        # eski entry yetim kalır ama tick'i yormaz, bellekten ölçek küçük).
        self._rate_last_reading: dict[str, TagReading] = {}
        # R-06: rate-of-change ve cross-sensor için ayrı cooldown haritaları.
        self._rate_cooldown: dict[str, datetime] = {}
        self._cross_cooldown: dict[int, datetime] = {}
        # H6 (review): cross-sensor kuralı (rule.id) → o kural için açtığımız
        # aktif (cleared olmayan, GERÇEK/non-test) alarm id'si. Koşul tekrar
        # sağlanınca auto-clear için kullanılır + aynı kural için ikinci alarm
        # açılmasını engeller (idempotent). Bakım test alarm'ları izlenmez.
        self._cross_active_alarm: dict[int, int] = {}

    async def start(self) -> None:
        """Engine'i başlatır — arka plan task olarak çalışır."""
        self._running = True
        await logger.ainfo(
            "Threshold engine başlatıldı",
            check_interval=self._check_interval,
        )
        try:
            while self._running:
                try:
                    await self._check_cycle()
                except Exception:
                    await logger.aerror(
                        "Threshold kontrol döngüsünde hata",
                        exc_info=True,
                    )
                await asyncio.sleep(self._check_interval)
        except asyncio.CancelledError:
            await logger.ainfo("Threshold engine iptal edildi")

    async def stop(self) -> None:
        """Engine'i durdurur."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        await logger.ainfo("Threshold engine durduruldu")

    async def _check_cycle(self) -> None:
        """Tek bir kontrol döngüsü — rate-of-change + cross-sensor (Layer-1).

        Threshold breach üretimi Critical loop'a taşındı (review H1); burada
        yalnız Layer-1 kuralları değerlendirilir.
        """
        # Bakım modu kontrolü için tag→instance haritası — rate-of-change ve
        # cross-sensor aynı haritayı paylaşır (tek query).
        bindings = await self._db.list_tag_bindings_all()
        self._tag_instance_map = {b.tag_id: b.instance_id for b in bindings}

        now = datetime.now(UTC)

        # Global bakım modu — tek sorgu, iki dalda kullanılır.
        global_test = await maintenance_mode.is_global_maintenance(
            self._db, now,
        )

        # R-06: Rate-of-change kontrolü — rate_of_change_threshold doldurulmuş
        # aktif tag'ler için.
        await self._check_rate_of_change(now, global_test)

        # R-06: Cross-sensor kontrolü — aktif kuralları tara, ihlali alarmla.
        await self._check_cross_sensor_rules(now, global_test)

    # --- R-06 Layer 1 ek kuralları ---

    async def _check_rate_of_change(
        self,
        now: datetime,
        global_test: bool,
    ) -> None:
        """V11-304: Aktif tag'lerden ``rate_of_change_threshold`` doldurulmuş
        olanlar için son iki okuma arasındaki delta'yı kontrol eder.

        Algoritma — tag başına:

        1. Eşik NULL ise (kontrol kapalı) atla.
        2. Cooldown içindeyse atla (5 dk).
        3. Son okumayı çek; önceki okuma hafızada yoksa kaydet ve geç.
        4. Aynı timestamp ise (yeni veri yok) atla.
        5. ``dt_minutes < 1 sn / 60`` ise sayısal hata büyür, atla.
        6. ``abs((value - prev_value) / dt_minutes) > threshold`` ise alarm.

        Hafıza güncellenir her durumda (alarm yazılsa da yazılmasa da)
        — bir sonraki tick'te aynı önceki noktaya bakmak yararsız.
        """
        active_tags: list[TagRecord] = await self._db.list_tags(status="active")
        candidates: list[TagRecord] = [
            t for t in active_tags
            if t.rate_of_change_threshold is not None
            and t.rate_of_change_threshold > 0
        ]
        if not candidates:
            return

        latest = await self._db.get_latest_tag_readings(
            [t.tag_id for t in candidates],
        )

        for tag in candidates:
            assert tag.rate_of_change_threshold is not None
            current = latest.get(tag.tag_id)
            if current is None:
                continue

            previous = self._rate_last_reading.get(tag.tag_id)
            # İlk tick — referans noktayı kaydet ve geç (delta hesaplanamaz).
            if previous is None:
                self._rate_last_reading[tag.tag_id] = current
                continue

            # Aynı reading (yeni veri yok) — atla, hafızayı güncelleme.
            if current.timestamp == previous.timestamp:
                continue

            dt_seconds = (current.timestamp - previous.timestamp).total_seconds()
            if dt_seconds < _RATE_MIN_DT_SECONDS:
                # Çok kısa aralık — sayısal hata büyük; bir sonraki tick'i bekle.
                self._rate_last_reading[tag.tag_id] = current
                continue

            delta_per_minute = abs(
                (current.value - previous.value) / (dt_seconds / 60.0),
            )
            # Hafızayı güncelle (her durumda).
            self._rate_last_reading[tag.tag_id] = current

            if delta_per_minute <= tag.rate_of_change_threshold:
                continue

            # Cooldown kontrolü — alarmı yazmadan önce.
            last_alarm = self._rate_cooldown.get(tag.tag_id)
            if last_alarm is not None and (now - last_alarm) < _RATE_OF_CHANGE_COOLDOWN:
                continue

            await self._raise_layer1_alarm(
                tag_id=tag.tag_id,
                source="rate_of_change",
                severity="warn",
                title=f"Custos Rate-of-change: {tag.name}",
                message=(
                    f"Tag {tag.tag_id} hızla değişiyor: "
                    f"{delta_per_minute:.2f}/dk (eşik: "
                    f"{tag.rate_of_change_threshold:.2f}/dk)"
                ),
                trigger_value=current.value,
                now=now,
                global_test=global_test,
            )
            self._rate_cooldown[tag.tag_id] = now

    async def _check_cross_sensor_rules(
        self,
        now: datetime,
        global_test: bool,
    ) -> None:
        """V11-305: Aktif cross-sensor kurallarını değerlendirir.

        Her kural için:
        1. Tag A ve Tag B'nin son okumalarını çek.
        2. Operator karşılaştırması (lt/gt/eq/neq/lte/gte) yap.
        3. Kural ihlali varsa (karşılaştırma False) alarm yaz.
        4. Cooldown 10 dk.

        Tag isimlerini mesaja eklemek için DB'den ayrı çekiyoruz — pilot
        ölçeğinde kural sayısı küçük (< 50), tag listesi tek query yeter.
        """
        rules: list[CrossSensorRule] = await self._db.list_cross_sensor_rules(
            enabled=True,
        )
        if not rules:
            return

        # Kuralda kullanılan unique tag id'leri topla (BIGINT id'ler).
        tag_ids: set[int] = set()
        for rule in rules:
            tag_ids.add(rule.tag_a_id)
            tag_ids.add(rule.tag_b_id)

        # Tag id (int) → TagRecord eşlemesi tek list_tags üzerinden;
        # pilot ölçeğinde 200-300 tag, in-memory filter ucuz.
        all_tags = await self._db.list_tags()
        tags_by_id: dict[int, TagRecord] = {
            t.id: t for t in all_tags if t.id is not None and t.id in tag_ids
        }

        # Tag'lerin son okumalarını tek round-trip'te al.
        tag_id_strs = [t.tag_id for t in tags_by_id.values()]
        latest = await self._db.get_latest_tag_readings(tag_id_strs)

        for rule in rules:
            assert rule.id is not None
            tag_a = tags_by_id.get(rule.tag_a_id)
            tag_b = tags_by_id.get(rule.tag_b_id)
            if tag_a is None or tag_b is None:
                # Tag silinmiş ama CASCADE henüz işlemedi (race) — bir sonraki
                # tick'te kural da düşecek; sessizce atla.
                continue

            reading_a = latest.get(tag_a.tag_id)
            reading_b = latest.get(tag_b.tag_id)
            if reading_a is None or reading_b is None:
                # Tag aktif ama henüz polling olmamış — değerlendirme yok.
                continue

            if _cross_sensor_holds(reading_a.value, rule.operator, reading_b.value):
                # Kural sağlanıyor — ihlal yok. Bu kural için daha önce açtığımız
                # aktif alarm varsa auto-clear et (review H6): çözülmüş bir
                # cross_sensor koşulu latch'lenip 30 dk sonra zorunlu crit'e
                # yükselmesin.
                await self._auto_clear_cross_sensor(rule.id, reading_a.value, now)
                continue

            # İhlal var. Bu kural için zaten aktif (gerçek) bir alarm izleniyorsa
            # tekrar açma (review H6): tek aktif alarm + spam önleme; koşul
            # çözülünce _auto_clear_cross_sensor kapatır.
            if rule.id in self._cross_active_alarm:
                continue

            # Cooldown kontrolü.
            last_alarm = self._cross_cooldown.get(rule.id)
            if last_alarm is not None and (now - last_alarm) < _CROSS_SENSOR_COOLDOWN:
                continue

            op_label = _CROSS_SENSOR_OPERATOR_LABEL.get(rule.operator, rule.operator)
            message = (
                f"Cross-sensor: '{tag_a.name}' {op_label} '{tag_b.name}' "
                f"kuralı ihlal edildi (a={reading_a.value:.2f}, "
                f"b={reading_b.value:.2f})"
            )
            # Alarm tag_id alanına Tag A'yı koyuyoruz — kural ekseni bu tag.
            # Kullanıcı alarm sayfasında "Tag A=value, Tag B=value" mesajını
            # görür, mesajda detay var.
            created = await self._raise_layer1_alarm(
                tag_id=tag_a.tag_id,
                source="cross_sensor",
                severity=rule.severity,
                title=f"Custos Cross-sensor: {rule.name}",
                message=message,
                trigger_value=reading_a.value,
                now=now,
                global_test=global_test,
            )
            self._cross_cooldown[rule.id] = now
            # Yalnız gerçek (non-test) alarmı izle: bakım test alarmı bittiğinde
            # süregelen ihlal için gerçek alarm üretilebilsin (idempotent guard
            # test alarmını latch'lemesin). Orphan test alarm'ı manuel clear ile
            # kapatılır.
            if not created.is_test and created.id is not None:
                self._cross_active_alarm[rule.id] = created.id

    async def _raise_layer1_alarm(
        self,
        *,
        tag_id: str,
        source: str,
        severity: str,
        title: str,
        message: str,
        trigger_value: float,
        now: datetime,
        global_test: bool,
    ) -> AlarmEvent:
        """Rate-of-change ve cross-sensor için ortak alarm yazma yolu.

        Oluşturulan ``AlarmEvent``'i döndürür (cross-sensor çağıranı non-test
        alarmı ``_cross_active_alarm``'da izlemek için id'sine ihtiyaç duyar).

        Bakım modu (per-instance + global) saygı, audit log, push gönderimi
        — threshold breach yolundaki davranışla aynı. Threshold ID yok
        (None); message + source kombinasyonu ile alarm sayfasında
        açıklayıcı satır oluşur (P-05 alarms.html).
        """
        is_test = global_test
        if not is_test:
            instance_id = self._tag_instance_map.get(tag_id)
            if instance_id is not None:
                is_test = await maintenance_mode.is_instance_in_maintenance(
                    self._db, instance_id, now,
                )

        created = await self._db.insert_alarm_event(
            AlarmEvent(
                threshold_id=None,
                tag_id=tag_id,
                state="triggered",
                triggered_at=now,
                trigger_value=trigger_value,
                is_test=is_test,
                source=source,
                severity=severity,
                message=message,
            ),
        )

        # Audit log — kategori is_test ve severity'ye göre threshold breach
        # ile aynı pattern.
        if is_test:
            audit_category = "maintenance_test_alarm"
            audit_action = f"{source}_test_triggered"
        elif severity == "emergency":
            audit_category = "alarm_emergency"
            audit_action = f"{source}_emergency_triggered"
        else:
            audit_category = "alarm"
            audit_action = f"{source}_triggered"
        await self._db.insert_audit_log(
            AuditLogEntry(
                category=audit_category,
                action=audit_action,
                entity_type="tag",
                entity_id=tag_id,
                detail=message,
            ),
        )

        await logger.ainfo(
            f"{source} alarm",
            tag_id=tag_id,
            severity=severity,
            is_test=is_test,
            message=message,
        )

        # Push — bakım modu zaten send_push_notifications içinde atlıyor.
        # alarm_id geçilince bildirim merkezinde her alarm ayrı satır.
        try:
            await send_push_notifications(
                db=self._db,
                title=title,
                body=message,
                severity=severity,
                is_test=is_test,
                alarm_id=created.id,
            )
        except Exception:
            await logger.awarning(
                f"{source} push bildirim gönderilemedi",
                tag_id=tag_id,
                exc_info=True,
            )

        return created

    async def _auto_clear_cross_sensor(
        self,
        rule_id: int,
        clear_value: float,
        now: datetime,
    ) -> None:
        """Cross-sensor kuralı tekrar sağlanınca aktif alarmı temizler (review H6).

        Yalnız bu oturumda açıp ``_cross_active_alarm``'da izlediğimiz gerçek
        alarm temizlenir; takip kaydı yoksa no-op. Cooldown'a DOKUNULMAZ — kısa
        sürede tekrar ihlal olursa cooldown alarm spam'ini önler.

        Kısıt (bellek-içi izleme): süreç yeniden başlarsa önceki oturumda
        açılmış bir cross_sensor alarmı (koşul downtime'da çözülmüşse) otomatik
        temizlenmez; operatör manuel ``clear`` ile kapatır.
        """
        alarm_id = self._cross_active_alarm.pop(rule_id, None)
        if alarm_id is None:
            return

        updated = await self._db.update_alarm_event(
            alarm_id,
            {"state": "cleared", "cleared_at": now, "clear_value": clear_value},
        )
        if updated is None:
            # Alarm bu arada başka yoldan kapanmış (manuel clear vb.) — sessiz geç.
            return

        await self._db.insert_audit_log(
            AuditLogEntry(
                category="alarm",
                action="cross_sensor_auto_cleared",
                entity_type="cross_sensor_rule",
                entity_id=str(rule_id),
                detail=(
                    f"Cross-sensor kuralı (id={rule_id}) tekrar sağlandı; "
                    f"alarm {alarm_id} otomatik temizlendi "
                    f"(değer={clear_value:.2f})"
                ),
            ),
        )
        await logger.ainfo(
            "Cross-sensor alarm otomatik temizlendi",
            rule_id=rule_id,
            alarm_id=alarm_id,
            clear_value=clear_value,
        )


def _cross_sensor_holds(value_a: float, operator: str, value_b: float) -> bool:
    """Cross-sensor karşılaştırması — kural sağlanıyorsa True (ihlal yok).

    Migration 036 CHECK constraint ile sınırlı altı operator: lt, gt, eq,
    neq, lte, gte. Bilinmeyen operator için True dönüp ihlal üretmiyoruz
    (defansif — DB constraint zaten engeller, ama UI/import yolu ile
    kötü değer gelirse alarm bombardımanı olmasın).
    """
    if operator == "lt":
        return value_a < value_b
    if operator == "gt":
        return value_a > value_b
    if operator == "eq":
        return value_a == value_b
    if operator == "neq":
        return value_a != value_b
    if operator == "lte":
        return value_a <= value_b
    if operator == "gte":
        return value_a >= value_b
    return True


# Kullanıcıya gösterilen operator etiketi — alarm mesajında kuralı okumayı
# kolaylaştırır. UI dropdown da aynı etiketi kullanır (cross_sensor_rules.html).
_CROSS_SENSOR_OPERATOR_LABEL: dict[str, str] = {
    "lt": "<",
    "gt": ">",
    "eq": "=",
    "neq": "≠",
    "lte": "≤",
    "gte": "≥",
}
