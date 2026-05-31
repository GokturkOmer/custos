"""Severity Escalation arka plan task'ı (R-06 / V11-306).

``warn`` severity ile tetiklenen **kullanıcı tanımlı** alarm operatör
tarafından zamanında ele alınmazsa otomatik olarak ``crit`` severity'ye
yükseltilir + push tetiklenir. Yükseltme süresi singleton
``retention_config.escalation_warn_to_crit_minutes`` (default 30,
range 5-240) ile globaldir.

**Kapsam (1 May 2026, kullanıcı kuralı):** Critical sadece kullanıcının
kendi tanımladığı kaynaklarda olur — yani ``source='threshold'`` veya
``source='cross_sensor'``. Otomatik üretilen alarmlar (liveness, anomaly,
spc, watchdog, rate_of_change) crit'e yükseltilmez; warn olarak kalır.
Operatör manuel olarak kapatır.

Algoritma — her tick'te (review M5/M6/H6b):

1. ``list_escalatable_alarms`` ile adaylar **DB tarafında** filtrelenir:
   ``state='triggered'`` (yalnız tetiklenmiş — acknowledge escalation'ı
   durdurur, H6b), ``warn``, ``escalated_from IS NULL``, ``is_test=false``,
   ``source IN _ESCALATABLE_SOURCES`` ve ``triggered_at <= now - eşik``.
   Sıralama en ESKİ önce (ASC) → ``_MAX_ALARMS_PER_TICK`` limiti yalnız
   gerçek adayları sayar (eski DESC LIMIT en eski alarm'ları pencere dışında
   bırakıyordu, M6).
2. Her aday ``escalate_alarm_to_crit`` ile **atomik** yükseltilir (M5):
   yalnız hâlâ triggered + yükseltilmemişse ``severity='crit'``,
   ``escalated_from='warn'``, ``escalated_at=now`` yazılır + push (crit kanalı)
   + audit log (``category='alarm_escalation'``). Eşzamanlı clear ile yarışta
   kapanmış alarma sahte crit/push gitmez (UPDATE 0 satır → push/audit atlanır).

Acknowledge artık escalation'ı durdurur (H6b): operatör onayladıysa alarm
crit'e yükseltilmez. Operatör onaylamaz ve koşul da çözülmezse warn → crit
eşiği dolunca yükseltilir. Bakım modu (``is_test=True``) alarm'ları hiç
yükseltilmez (DB filtresi).

Kanonik kaynaklar:
- ``shared/database.py`` — AlarmEvent dataclass'ında escalated_from /
  escalated_at; _ALLOWED_ALARM_EVENT_UPDATE_FIELDS bu üçlüyü içerir.
- ``analytics/push_sender.py`` — severity-based filter.
- ``__main__.py:lifespan`` — bu loop'u bir asyncio task olarak başlatır.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Final

import structlog

from custos.analytics.push_sender import send_push_notifications
from custos.shared.database import (
    AuditLogEntry,
    DatabaseInterface,
)

logger = structlog.get_logger(logger_name="escalation")


# Tick aralığı — 60 saniye. retention_config eşiği dakika cinsinden;
# 60 sn doğruluk pilot için yeterli (10-30 sn'lik gecikme operatöre
# fark etmez).
_TICK_INTERVAL_SECONDS: Final[float] = 60.0

# Aynı tick'te çekilen aktif warn alarmlarının üst limiti — fonksiyonel
# büyük ölçek değil, bombardıman koruması (yanlış config 1000+ alarm
# açtıysa hepsini tek tick'te yükseltmek istemiyoruz). Pilot ölçeğinde
# 200 yeter; aşılırsa bir sonraki tick'te kalan kayıt işlenir.
_MAX_ALARMS_PER_TICK: Final[int] = 200

# Yalnızca kullanıcı tanımlı kaynaklar warn → crit yükseltilir. Otomatik
# kaynaklar (liveness, anomaly, spc, watchdog, rate_of_change) operatörün
# kararı olmadan crit'e çıkamaz; warn'da kalır. Kullanıcı kuralı (1 May
# 2026): "Critical alarm sadece kullanıcının kendi belirlediği eşik veya
# sistem üzerinde olur." threshold + cross_sensor: ikisi de UI'dan kullanıcı
# tarafından tanımlanır.
_ESCALATABLE_SOURCES: Final[frozenset[str]] = frozenset({"threshold", "cross_sensor"})


class EscalationLoop:
    """warn → crit otomatik yükseltme döngüsü.

    Threshold engine ve liveness engine ile aynı arka plan task deseni:
    ``start()`` döngüsü kendi tick periyoduyla çalışır, hatalar yutulur ve
    loglanır, ``CancelledError`` ile temiz çıkış. Push bildirimi ve audit
    log her başarılı yükseltme sonrası tetiklenir.
    """

    def __init__(
        self,
        db: DatabaseInterface,
        tick_interval_seconds: float = _TICK_INTERVAL_SECONDS,
    ) -> None:
        self._db = db
        self._tick_interval = tick_interval_seconds
        self._running = False

    async def start(self) -> None:
        """Loop'u başlatır — arka plan task olarak çalışır."""
        self._running = True
        await logger.ainfo(
            "Escalation loop başlatıldı",
            tick_interval=self._tick_interval,
        )
        try:
            while self._running:
                try:
                    await self._tick()
                except Exception:
                    await logger.aerror(
                        "Escalation tick hatası",
                        exc_info=True,
                    )
                try:
                    await asyncio.sleep(self._tick_interval)
                except asyncio.CancelledError:
                    break
        finally:
            await logger.ainfo("Escalation loop durdu")

    async def stop(self) -> None:
        """Loop'u durdurur."""
        self._running = False

    async def _tick(self) -> None:
        """Tek tarama: yükseltme süresini aşmış warn alarm'larını crit'e çıkarır."""
        now = datetime.now(UTC)

        # Eşik singleton retention_config'ten okunur — kullanıcı 5-240 dk
        # arasında ayarlayabilir, runtime'da setting değişince bir sonraki
        # tick'te etkili olur.
        cfg = await self._db.get_retention_config()
        threshold_minutes = cfg.escalation_warn_to_crit_minutes
        triggered_before = now - timedelta(minutes=threshold_minutes)

        # Adaylar DB tarafında filtrelenir (review M6/H6b): yalnız triggered +
        # warn + yükseltilmemiş + test değil + kullanıcı kaynak + yaşı eşiği
        # aşmış; en ESKİ önce. Böylece _MAX_ALARMS_PER_TICK limiti yalnız gerçek
        # adayları sayar (eski DESC LIMIT en eski alarm'ları kaçırıyordu).
        candidates = await self._db.list_escalatable_alarms(
            sources=sorted(_ESCALATABLE_SOURCES),
            triggered_before=triggered_before,
            limit=_MAX_ALARMS_PER_TICK,
        )

        escalated_count = 0
        for alarm in candidates:
            if alarm.id is None or alarm.triggered_at is None:
                continue
            age_seconds = (now - alarm.triggered_at).total_seconds()
            if await self._escalate_alarm(
                alarm_id=alarm.id,
                tag_id=alarm.tag_id,
                old_severity=alarm.severity,
                threshold_minutes=threshold_minutes,
                age_seconds=age_seconds,
                now=now,
            ):
                escalated_count += 1

        if escalated_count > 0:
            await logger.ainfo(
                "Escalation tick tamamlandı",
                escalated_count=escalated_count,
                threshold_minutes=threshold_minutes,
            )

    async def _escalate_alarm(
        self,
        *,
        alarm_id: int,
        tag_id: str,
        old_severity: str,
        threshold_minutes: int,
        age_seconds: float,
        now: datetime,
    ) -> bool:
        """Tek bir alarmı atomik olarak warn → crit yükseltir + push + audit log.

        Atomik UPDATE (review M5) yalnız alarm hâlâ triggered + yükseltilmemişse
        uygulanır; eşzamanlı clear/acknowledge ile yarışta durumu değiştiyse
        ``None`` döner → push/audit atlanır ve ``False`` döndürülür. Yükseltme
        gerçekten uygulandıysa ``True``.
        """
        updated = await self._db.escalate_alarm_to_crit(
            alarm_id,
            old_severity=old_severity,
            escalated_at=now,
        )
        if updated is None:
            # Yarışta kapanmış/onaylanmış veya zaten yükseltilmiş — sahte crit
            # + push yazma, sessizce atla (review M5).
            await logger.ainfo(
                "Escalation atlandı (alarm artık triggered değil veya yükseltilmiş)",
                alarm_id=alarm_id,
            )
            return False

        await self._db.insert_audit_log(
            AuditLogEntry(
                category="alarm_escalation",
                action="warn_to_crit",
                entity_type="alarm_event",
                entity_id=str(alarm_id),
                detail=(
                    f"Alarm {alarm_id} (tag={tag_id}) {old_severity} → crit "
                    f"yükseltildi (yaş={int(age_seconds)}s, "
                    f"eşik={threshold_minutes}dk)"
                ),
            ),
        )

        await logger.awarning(
            "Alarm severity yükseltildi",
            alarm_id=alarm_id,
            tag_id=tag_id,
            old_severity=old_severity,
            new_severity="crit",
            age_seconds=int(age_seconds),
            threshold_minutes=threshold_minutes,
        )

        # Push — crit kanalı; sessiz saat varsa atlanır (push_sender ele alır).
        # is_test=False çünkü DB filtresi (list_escalatable_alarms) zaten test
        # alarm'larını eler.
        try:
            await send_push_notifications(
                db=self._db,
                title="Custos Escalation: warn → crit",
                body=(
                    f"Tag {tag_id} alarmı {threshold_minutes} dk açık kaldı; "
                    f"crit'e yükseltildi."
                ),
                severity="crit",
                is_test=False,
            )
        except Exception:
            await logger.awarning(
                "Escalation push bildirim gönderilemedi",
                alarm_id=alarm_id,
                exc_info=True,
            )
        return True
