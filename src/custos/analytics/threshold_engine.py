"""ISA-18.2 uyumlu Threshold Engine.

Analytics sürecinde periyodik olarak çalışan arka plan task'ı.
Aktif tag'lerin son değerlerini threshold tanımlarıyla karşılaştırır,
alarm event'leri oluşturur/günceller.

Debounce: Eşik aşımının belirli süre devam etmesi gerekir.
Hysteresis: Alarm temizleme için ölü bant (set_point ± hysteresis).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import structlog

from custos.analytics.push_sender import send_push_notifications
from custos.shared.database import (
    AlarmEvent,
    AuditLogEntry,
    DatabaseInterface,
    Threshold,
)

logger = structlog.get_logger(logger_name="threshold_engine")


class ThresholdEngine:
    """ISA-18.2 alarm state machine.

    Periyodik olarak aktif tag'lerin son değerlerini kontrol eder,
    threshold tanımlarıyla karşılaştırır, alarm event'leri oluşturur/günceller.
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
        # Debounce izleyici: threshold_id → ilk eşik aşım zamanı
        self._debounce_tracker: dict[int, datetime] = {}

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
        """Tek bir kontrol döngüsü — tüm aktif threshold'ları değerlendirir."""
        # 1. Aktif threshold'ları çek
        thresholds = await self._db.list_thresholds(enabled=True)
        if not thresholds:
            return

        # 2. İlgili tag'lerin son değerlerini çek
        tag_ids = list({t.tag_id for t in thresholds})
        readings = await self._db.get_latest_tag_readings(tag_ids)

        now = datetime.now(UTC)

        # 3. Her threshold için değerlendir
        for threshold in thresholds:
            assert threshold.id is not None
            reading = readings.get(threshold.tag_id)
            if reading is None:
                # Tag için okuma yok — debounce tracker'dan temizle
                self._debounce_tracker.pop(threshold.id, None)
                continue

            value = reading.value
            breach = _is_breach(threshold, value)

            # Mevcut aktif alarm var mı?
            active_alarm = await self._db.get_active_alarm_for_threshold(
                threshold.id,
            )

            if breach and active_alarm is None:
                # Durum A: Eşik aşılmış, aktif alarm YOK → debounce kontrolü
                await self._handle_breach_no_alarm(threshold, value, now)

            elif breach and active_alarm is not None:
                # Durum B: Eşik aşılmış, aktif alarm VAR → devam ediyor
                pass

            elif not breach and active_alarm is not None:
                # Durum C: Eşik aşılmamış, aktif alarm VAR → hysteresis kontrolü
                await self._handle_no_breach_with_alarm(
                    threshold,
                    active_alarm,
                    value,
                    now,
                )

            else:
                # Durum D: Eşik aşılmamış, aktif alarm YOK → normal
                self._debounce_tracker.pop(threshold.id, None)

    async def _handle_breach_no_alarm(
        self,
        threshold: Threshold,
        value: float,
        now: datetime,
    ) -> None:
        """Eşik aşılmış ama aktif alarm yok — debounce kontrolü yap."""
        assert threshold.id is not None
        tid = threshold.id

        if tid not in self._debounce_tracker:
            # İlk aşım — tracker'a kaydet
            self._debounce_tracker[tid] = now
            return

        first_breach = self._debounce_tracker[tid]
        elapsed = (now - first_breach).total_seconds()

        if elapsed < threshold.debounce_seconds:
            # Debounce süresi dolmamış
            return

        # Debounce süresi doldu → alarm tetikle
        event = AlarmEvent(
            threshold_id=tid,
            tag_id=threshold.tag_id,
            state="triggered",
            triggered_at=now,
            trigger_value=value,
        )
        created = await self._db.insert_alarm_event(event)

        # Audit log
        await self._db.insert_audit_log(
            AuditLogEntry(
                category="alarm",
                action="triggered",
                entity_type="threshold",
                entity_id=str(tid),
                detail=(
                    f"Alarm tetiklendi: {threshold.name} "
                    f"(tag={threshold.tag_id}, değer={value:.2f}, "
                    f"eşik={threshold.set_point:.2f}, yön={threshold.direction})"
                ),
            ),
        )

        # Tracker'dan temizle (alarm oluştu)
        self._debounce_tracker.pop(tid, None)

        await logger.ainfo(
            "Alarm tetiklendi",
            threshold_id=tid,
            threshold_name=threshold.name,
            tag_id=threshold.tag_id,
            value=value,
            set_point=threshold.set_point,
            alarm_event_id=created.id,
        )

        # Push bildirim gönder
        try:
            await send_push_notifications(
                db=self._db,
                title=f"Custos Alarm: {threshold.name}",
                body=(
                    f"Tag {threshold.tag_id} = {value:.2f} "
                    f"(eşik: {threshold.set_point:.2f}, yön: {threshold.direction})"
                ),
                severity=threshold.severity,
            )
        except Exception:
            await logger.awarning(
                "Push bildirim gönderilemedi",
                threshold_id=tid,
                exc_info=True,
            )

    async def _handle_no_breach_with_alarm(
        self,
        threshold: Threshold,
        alarm: AlarmEvent,
        value: float,
        now: datetime,
    ) -> None:
        """Eşik aşılmamış ama aktif alarm var — hysteresis kontrolü yap."""
        assert threshold.id is not None
        assert alarm.id is not None

        can_clear = _can_clear_with_hysteresis(threshold, value)

        if not can_clear:
            # Hysteresis bandı içinde — temizleme yok
            return

        # Alarm temizle
        await self._db.update_alarm_event(
            alarm.id,
            {
                "state": "cleared",
                "cleared_at": now,
                "clear_value": value,
            },
        )

        # Audit log
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
            "Alarm temizlendi",
            threshold_id=threshold.id,
            threshold_name=threshold.name,
            tag_id=threshold.tag_id,
            clear_value=value,
            alarm_event_id=alarm.id,
        )


def _is_breach(threshold: Threshold, value: float) -> bool:
    """Değerin eşiği aşıp aşmadığını kontrol eder."""
    if threshold.direction == "high":
        return value >= threshold.set_point
    # direction == 'low'
    return value <= threshold.set_point


def _can_clear_with_hysteresis(threshold: Threshold, value: float) -> bool:
    """Hysteresis bandını geçip geçmediğini kontrol eder."""
    if threshold.direction == "high":
        return value < threshold.set_point - threshold.hysteresis
    # direction == 'low'
    return value > threshold.set_point + threshold.hysteresis
