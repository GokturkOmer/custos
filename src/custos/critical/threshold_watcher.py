"""Eşik (threshold) izleyici — Critical loop alarm üretimi (review H1 / ADR-001).

Kullanıcı-tanımlı alt/üst limit alarmları ARTIK Critical loop'ta üretilir;
böylece alarm üretimi Analytics'in (ML/dashboard/eğitim) çökmesinden veya event
loop bloklamasından izole olur — ADR-001'in çekirdek vaadi nihayet kodda
karşılanır.

Tasarım:
- ``ThresholdWatcher`` Collector ile AYNI event loop'ta AYRI bir task olarak
  koşar. Collector'ın her tick'te yayımladığı **in-memory** son okumaları
  (``reading_source``) okur — DB'den ``get_latest_tag_readings`` round-trip'i
  YOK (Critical zaten değeri elinde tutuyor).
- Breach tespiti + debounce in-memory ve ucuz; alarm YAZIMI (seyrek,
  edge-triggered) ``DatabaseInterface`` üzerinden yapılır. Bu task ayrı olduğu
  için yazım/okuma Collector'ın polling ritmini BLOKLAMAZ.
- **Push GÖNDERİLMEZ.** Critical alarm'ı ``alarm_events``'e yazar; pywebpush +
  VAPID + abonelikler Analytics'e ait kalır (minimal bağımlılık). Analytics'teki
  push-dispatch loop'u ``pushed_at IS NULL`` alarm'ları gönderir.
- Her threshold kendi ``try/except``'inde değerlendirilir (review C1 dersi):
  tek bozuk threshold tüm cycle'ı düşürmez.

Mimari kural (CLAUDE.md): Bu modül Critical loop içinde olduğu için yalnızca
soyut DB arayüzü (``DatabaseInterface``) + ``shared`` saf yardımcılarını
kullanır. asyncpg/SQL/ORM/ML YASAK; Modbus yazma yok (zaten Modbus'a dokunmaz).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime

import structlog

from custos.shared.database import (
    AlarmEvent,
    AuditLogEntry,
    DatabaseInterface,
    TagReading,
    Threshold,
)
from custos.shared.maintenance import (
    is_global_maintenance,
    is_instance_in_maintenance,
)
from custos.shared.threshold_core import (
    can_clear_with_hysteresis,
    effective_debounce_seconds,
    is_breach,
)

logger = structlog.get_logger(logger_name="threshold_watcher")

# Değerlendirme aralığı (sn). Analytics'teki eski 5 sn yerine daha sık; emergency
# yanıt süresini kısaltır (review M8) ama threshold başına tek get_active_alarm
# okuması ile pilot ölçeğinde DB yükü ihmal edilebilir.
_DEFAULT_CHECK_INTERVAL_SECONDS = 2.0

# Threshold tanımı + tag→instance haritası yenileme aralığı (cycle sayısı).
# Collector'ın tag hot-reload deseniyle (~60 tick) uyumlu; operatör Settings'ten
# eşik eklerse en geç bu kadar cycle sonra devreye girer.
_DEFAULT_REFRESH_INTERVAL_CYCLES = 30


class ThresholdWatcher:
    """Collector'ın yayımladığı son değerleri eşiklerle karşılaştırıp alarm yazar.

    Push göndermez; bakım modunda ``is_test=True`` yazar; emergency auto-clear
    bypass'ı ve bakım-sonrası gerçek-alarm yeniden değerlendirmesi (H5) uygular.
    """

    def __init__(
        self,
        db: DatabaseInterface,
        reading_source: Callable[[], dict[str, TagReading]],
        check_interval_seconds: float = _DEFAULT_CHECK_INTERVAL_SECONDS,
        refresh_interval_cycles: int = _DEFAULT_REFRESH_INTERVAL_CYCLES,
    ) -> None:
        self._db = db
        self._reading_source = reading_source
        self._check_interval = check_interval_seconds
        self._refresh_interval = max(1, refresh_interval_cycles)
        self._running = False
        self._cycle_count = 0
        # Debounce izleyici: threshold_id → ilk eşik aşım zamanı (UTC).
        self._debounce_tracker: dict[int, datetime] = {}
        # Aktif threshold tanımları (periyodik yenilenir).
        self._thresholds: list[Threshold] = []
        # tag_id → instance_id (bakım kontrolü için; binding yoksa entry yok).
        self._tag_instance_map: dict[str, int] = {}

    async def start(self) -> None:
        """İzleyiciyi başlatır — arka plan task olarak çalışır."""
        self._running = True
        await self._refresh_definitions()
        await logger.ainfo(
            "Threshold watcher başlatıldı",
            check_interval=self._check_interval,
            threshold_sayısı=len(self._thresholds),
        )
        try:
            while self._running:
                try:
                    await self._evaluate_cycle()
                except Exception:
                    await logger.aerror(
                        "Threshold değerlendirme döngüsünde hata",
                        exc_info=True,
                    )

                self._cycle_count += 1
                if self._cycle_count >= self._refresh_interval:
                    self._cycle_count = 0
                    await self._refresh_definitions()

                try:
                    await asyncio.sleep(self._check_interval)
                except asyncio.CancelledError:
                    break
        except asyncio.CancelledError:
            await logger.ainfo("Threshold watcher iptal edildi")

    async def stop(self) -> None:
        """İzleyiciyi durdurur (task iptali __main__ tarafından yapılır)."""
        self._running = False
        await logger.ainfo("Threshold watcher durduruluyor")

    async def _refresh_definitions(self) -> None:
        """Aktif threshold'ları + tag→instance haritasını DB'den yeniler.

        Hata durumunda eski tanımlar korunur (alarm üretimi sessizce durmasın).
        """
        try:
            self._thresholds = await self._db.list_thresholds(enabled=True)
            bindings = await self._db.list_tag_bindings_all()
            self._tag_instance_map = {b.tag_id: b.instance_id for b in bindings}
        except Exception:
            await logger.aerror(
                "Threshold tanımları yenilenemedi — eski tanımlar korunuyor",
                exc_info=True,
            )

    async def _evaluate_cycle(self) -> None:
        """Tek değerlendirme cycle'ı — Collector'ın son okumalarıyla."""
        if not self._thresholds:
            return

        readings = self._reading_source()
        if not readings:
            return

        now = datetime.now(UTC)
        for threshold in self._thresholds:
            try:
                await self._evaluate_threshold(threshold, readings, now)
            except Exception:
                # Per-threshold izolasyon (C1): tek bozuk threshold/config tüm
                # cycle'ı düşürmesin; diğer eşikler değerlendirilmeye devam eder.
                await logger.aerror(
                    "Threshold değerlendirilemedi",
                    threshold_id=threshold.id,
                    exc_info=True,
                )

    async def _evaluate_threshold(
        self,
        threshold: Threshold,
        readings: dict[str, TagReading],
        now: datetime,
    ) -> None:
        """Tek bir threshold'u değerlendirir: breach state machine + alarm yazımı."""
        assert threshold.id is not None
        reading = readings.get(threshold.tag_id)
        if reading is None:
            # Tag bu cycle'da bilinmiyor (henüz okunmamış/inaktif) — debounce sıfırla.
            self._debounce_tracker.pop(threshold.id, None)
            return

        value = reading.value
        breach = is_breach(threshold, value)
        active = await self._db.get_active_alarm_for_threshold(threshold.id)

        if breach and active is None:
            await self._handle_breach_no_alarm(threshold, value, now)
        elif breach and active is not None:
            await self._handle_breach_with_alarm(threshold, active, value, now)
        elif not breach and active is not None:
            await self._handle_no_breach_with_alarm(threshold, active, value, now)
        else:
            self._debounce_tracker.pop(threshold.id, None)

    async def _compute_is_test(self, threshold: Threshold, now: datetime) -> bool:
        """Breach şu an bakım modunda mı (global veya per-instance)? → is_test."""
        if await is_global_maintenance(self._db, now):
            return True
        instance_id = self._tag_instance_map.get(threshold.tag_id)
        if instance_id is not None:
            return await is_instance_in_maintenance(self._db, instance_id, now)
        return False

    @staticmethod
    def _build_message(threshold: Threshold, value: float) -> str:
        """Push gövdesi + alarm satırı için kendi-kendine yeten açıklama."""
        return (
            f"{threshold.name}: tag {threshold.tag_id} = {value:.2f} "
            f"(eşik {threshold.set_point:.2f}, yön {threshold.direction})"
        )

    async def _handle_breach_no_alarm(
        self,
        threshold: Threshold,
        value: float,
        now: datetime,
    ) -> None:
        """Eşik aşıldı ama aktif alarm yok — debounce sonrası alarm yaz (push YOK)."""
        assert threshold.id is not None
        tid = threshold.id

        if tid not in self._debounce_tracker:
            self._debounce_tracker[tid] = now
            return

        elapsed = (now - self._debounce_tracker[tid]).total_seconds()
        if elapsed < effective_debounce_seconds(threshold):
            return

        is_test = await self._compute_is_test(threshold, now)
        message = self._build_message(threshold, value)
        created = await self._db.insert_alarm_event(
            AlarmEvent(
                threshold_id=tid,
                tag_id=threshold.tag_id,
                state="triggered",
                triggered_at=now,
                trigger_value=value,
                is_test=is_test,
                source="threshold",
                severity=threshold.severity,
                message=message,
            ),
        )

        if is_test:
            audit_category, audit_action = "maintenance_test_alarm", "test_triggered"
        elif threshold.severity == "emergency":
            audit_category, audit_action = "alarm_emergency", "emergency_alarm_triggered"
        else:
            audit_category, audit_action = "alarm", "triggered"
        await self._db.insert_audit_log(
            AuditLogEntry(
                category=audit_category,
                action=audit_action,
                entity_type="threshold",
                entity_id=str(tid),
                detail=(
                    f"Alarm tetiklendi: {threshold.name} "
                    f"(tag={threshold.tag_id}, değer={value:.2f}, "
                    f"eşik={threshold.set_point:.2f}, yön={threshold.direction}, "
                    f"severity={threshold.severity}, is_test={is_test})"
                ),
            ),
        )

        self._debounce_tracker.pop(tid, None)
        await logger.ainfo(
            "Alarm tetiklendi (critical)",
            threshold_id=tid,
            tag_id=threshold.tag_id,
            value=value,
            set_point=threshold.set_point,
            alarm_event_id=created.id,
            is_test=is_test,
        )

    async def _handle_breach_with_alarm(
        self,
        threshold: Threshold,
        alarm: AlarmEvent,
        value: float,
        now: datetime,
    ) -> None:
        """Eşik aşılı + aktif alarm var. Normalde no-op; H5 istisnası:

        Aktif alarm bir bakım test alarmıysa (``is_test=True``) ama breach ARTIK
        bakım modunda değilse → test alarmını kapat + debounce sıfırla. Böylece
        süregelen breach bir sonraki cycle'da GERÇEK operatör alarmı üretir
        (bakım penceresinde başlayıp bakım bitince gölgede kalan arıza kaybolmaz).
        """
        assert threshold.id is not None
        assert alarm.id is not None

        if alarm.is_test and not await self._compute_is_test(threshold, now):
            await self._db.update_alarm_event(
                alarm.id,
                {"state": "cleared", "cleared_at": now, "clear_value": value},
            )
            self._debounce_tracker.pop(threshold.id, None)
            await self._db.insert_audit_log(
                AuditLogEntry(
                    category="alarm",
                    action="test_cleared_post_maintenance",
                    entity_type="threshold",
                    entity_id=str(threshold.id),
                    detail=(
                        f"Bakım sonrası test alarmı kapatıldı, süregelen breach "
                        f"gerçek alarm olarak yeniden değerlendirilecek: "
                        f"{threshold.name} (tag={threshold.tag_id})"
                    ),
                ),
            )
            await logger.ainfo(
                "Bakım sonrası test alarmı kapatıldı (H5)",
                threshold_id=threshold.id,
                tag_id=threshold.tag_id,
                alarm_event_id=alarm.id,
            )
        # Aksi halde: breach sürüyor, alarm zaten açık → ek işlem yok.

    async def _handle_no_breach_with_alarm(
        self,
        threshold: Threshold,
        alarm: AlarmEvent,
        value: float,
        now: datetime,
    ) -> None:
        """Eşik aşılmıyor + aktif alarm var — hysteresis ile temizle."""
        assert threshold.id is not None
        assert alarm.id is not None

        # Emergency auto-clear bypass (V11-107/K10): yanlışlıkla tetiklenen
        # emergency'nin sessiz temizlenmesi hayati riski gizleyebilir — operatör
        # manuel onaylamalı.
        if threshold.severity == "emergency":
            return

        if not can_clear_with_hysteresis(threshold, value):
            return

        await self._db.update_alarm_event(
            alarm.id,
            {"state": "cleared", "cleared_at": now, "clear_value": value},
        )
        await self._db.insert_audit_log(
            AuditLogEntry(
                category="alarm",
                action="cleared",
                entity_type="threshold",
                entity_id=str(threshold.id),
                detail=(
                    f"Alarm temizlendi: {threshold.name} "
                    f"(tag={threshold.tag_id}, temiz değer={value:.2f})"
                ),
            ),
        )
        await logger.ainfo(
            "Alarm temizlendi (critical)",
            threshold_id=threshold.id,
            tag_id=threshold.tag_id,
            clear_value=value,
            alarm_event_id=alarm.id,
        )
