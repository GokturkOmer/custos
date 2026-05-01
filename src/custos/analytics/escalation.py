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

Algoritma — her tick'te:

1. Aktif (``cleared_at IS NULL``), ``warn`` severity, henüz yükseltilmemiş
   (``escalated_from IS NULL``), ``source IN _ESCALATABLE_SOURCES`` alarm'ları çek.
2. ``now - triggered_at >= threshold_minutes`` ise:
   - ``severity='crit'``, ``escalated_from='warn'``, ``escalated_at=now``
   - Push gönder (crit kanalı)
   - Audit log (``category='alarm_escalation'``)

Bakım modu (``is_test=True``) alarm'ları yükseltilse bile push atlanır
(``send_push_notifications`` zaten ``is_test`` flag'i ile early-return
yapar). Test alarm'larını yükseltmemek de mantıklı bir karar olabilir;
şu anki tercihimiz: ``is_test=True`` ise yükseltme yapmıyoruz (üretim
bombardımanına girmesin diye, R-06 paket dokümanında "warn 30 dk → crit"
sade kuralı kullanıcıya sözleniyor — bakım kalıcı dahil).

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
        """Tek tarama: warn alarm'larını yükseltme süresine göre kontrol eder."""
        now = datetime.now(UTC)

        # Eşik singleton retention_config'ten okunur — kullanıcı 5-240 dk
        # arasında ayarlayabilir, runtime'da setting değişince bir sonraki
        # tick'te etkili olur.
        cfg = await self._db.get_retention_config()
        threshold_minutes = cfg.escalation_warn_to_crit_minutes
        threshold_delta = timedelta(minutes=threshold_minutes)

        # Aktif (cleared olmayan) warn alarmları — list_alarm_events helper'ı
        # state filtresi alıyor; "triggered" + "acknowledged" iki çağrı.
        # Acknowledged warn alarm da yükseltmeye dahil — operatör onayladıktan
        # sonra kapatmazsa hâlâ açık demek, escalation devam eder.
        triggered = await self._db.list_alarm_events(
            state="triggered", limit=_MAX_ALARMS_PER_TICK,
        )
        acknowledged = await self._db.list_alarm_events(
            state="acknowledged", limit=_MAX_ALARMS_PER_TICK,
        )
        candidates = triggered + acknowledged

        escalated_count = 0
        skipped_non_user_source = 0
        for alarm in candidates:
            if alarm.id is None:
                continue
            if alarm.severity != "warn":
                continue
            if alarm.escalated_from is not None:
                # Zaten yükseltilmiş — dokunma.
                continue
            if alarm.is_test:
                # Bakım modu alarm'ı — yükseltme yok (push gitmiyor zaten,
                # operasyonel sinyal değil).
                continue
            if alarm.source not in _ESCALATABLE_SOURCES:
                # Otomatik kaynak (liveness/anomaly/spc/watchdog/rate_of_change)
                # — kullanıcı tanımlı değil, crit'e çıkmaz.
                skipped_non_user_source += 1
                continue
            if alarm.triggered_at is None:
                continue

            age = now - alarm.triggered_at
            if age < threshold_delta:
                continue

            await self._escalate_alarm(
                alarm_id=alarm.id,
                tag_id=alarm.tag_id,
                old_severity=alarm.severity,
                threshold_minutes=threshold_minutes,
                age_seconds=age.total_seconds(),
                now=now,
            )
            escalated_count += 1

        if escalated_count > 0 or skipped_non_user_source > 0:
            await logger.ainfo(
                "Escalation tick tamamlandı",
                escalated_count=escalated_count,
                skipped_non_user_source=skipped_non_user_source,
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
    ) -> None:
        """Tek bir alarmı warn → crit yükseltir + push + audit log."""
        # Update — severity, escalated_from, escalated_at tek round-trip.
        # ``_ALLOWED_ALARM_EVENT_UPDATE_FIELDS`` bu üçlüyü içermek zorunda
        # (R-06 / Migration 036); aksi ValueError fırlar.
        updated = await self._db.update_alarm_event(
            alarm_id,
            {
                "severity": "crit",
                "escalated_from": old_severity,
                "escalated_at": now,
            },
        )
        if updated is None:
            await logger.awarning(
                "Escalation: alarm bulunamadı",
                alarm_id=alarm_id,
            )
            return

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
        # is_test=False çünkü _tick zaten test alarm'larını filtreliyor.
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
