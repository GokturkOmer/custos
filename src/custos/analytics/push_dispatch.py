"""Push-dispatch loop — Critical'ın yazdığı threshold alarm'larını iletir.

review H1 cutover: eşik tabanlı alarm üretimi Critical loop'a taşındı; Critical
alarm'ı ``alarm_events``'e yazar ama PUSH GÖNDERMEZ (pywebpush + VAPID +
abonelikler Analytics'e ait — Critical minimal bağımlılık). Bu loop, henüz
iletilmemiş (``pushed_at IS NULL``), test olmayan ``source='threshold'``
alarm'larını periyodik çeker, push gönderir ve ``pushed_at``'i set ederek
tek-sefer iletim sağlar.

Diğer kaynaklar (rate_of_change / cross_sensor / liveness / spc / escalation)
push'larını kendi yollarında inline gönderir; bu loop onlara DOKUNMAZ
(``list_pending_threshold_push_alarms`` yalnız ``source='threshold'`` döner).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import structlog

from custos.analytics.push_sender import send_push_notifications
from custos.shared.database import AlarmEvent, DatabaseInterface

logger = structlog.get_logger(logger_name="push_dispatch")

# Dispatch tarama aralığı (sn). Push gerçek-zamanlı-kritik değil; ~2 sn gecikme
# bildirim için kabul edilebilir, bekleyen sorgusu kısmi indeksle çok ucuz.
_DISPATCH_INTERVAL_SECONDS: float = 2.0

# Tek cycle'da işlenecek maksimum alarm (alarm fırtınasında loop'u sınırlar).
_DISPATCH_BATCH_LIMIT: int = 50

# Severity → kullanıcıya gösterilen başlık etiketi.
_SEVERITY_LABEL: dict[str, str] = {
    "emergency": "ACİL",
    "crit": "Kritik",
    "warn": "Uyarı",
    "info": "Bilgi",
}


def _build_title_body(alarm: AlarmEvent) -> tuple[str, str]:
    """Alarm satırından push başlığı + gövdesi üretir.

    Critical, threshold alarmını yazarken ``message``'i kendi-kendine yeten
    açıklamayla doldurur; gövde olarak onu kullanırız (yoksa minimal fallback).
    """
    label = _SEVERITY_LABEL.get(alarm.severity, "Alarm")
    title = f"Custos {label}"
    body = alarm.message or f"Tag {alarm.tag_id} = {alarm.trigger_value:.2f}"
    return title, body


async def dispatch_once(db: DatabaseInterface) -> int:
    """Bekleyen threshold alarm'larını tek seferde iletir + işaretler.

    İletim denemesi yapılan (pushed_at set edilen) alarm sayısını döndürür.
    Test edilebilirlik için loop dışı public.
    """
    pending = await db.list_pending_threshold_push_alarms(limit=_DISPATCH_BATCH_LIMIT)
    if not pending:
        return 0

    pushed_ids: list[int] = []
    for alarm in pending:
        if alarm.id is None:
            continue
        title, body = _build_title_body(alarm)
        try:
            await send_push_notifications(
                db=db,
                title=title,
                body=body,
                severity=alarm.severity,
                is_test=alarm.is_test,
                alarm_id=alarm.id,
            )
            # Deneme yapıldı → işaretle. (master-off / abonelik-yok / sessiz saat
            # → 0 gidebilir; inline davranışla tutarlı: push tek-sefer denenir.)
            pushed_ids.append(alarm.id)
        except Exception:
            # Beklenmeyen hata (geçici DB/ağ) — İŞARETLEME; bir sonraki cycle
            # tekrar denesin. Tek bozuk alarm batch'i düşürmez (C1 dersi).
            await logger.awarning(
                "Threshold alarm push dispatch başarısız — tekrar denenecek",
                alarm_event_id=alarm.id,
                exc_info=True,
            )

    if pushed_ids:
        await db.mark_alarms_pushed(pushed_ids, datetime.now(UTC))
    return len(pushed_ids)


async def push_dispatch_loop(
    db: DatabaseInterface,
    interval_seconds: float = _DISPATCH_INTERVAL_SECONDS,
) -> None:
    """Arka plan task: bekleyen threshold alarm'larını periyodik iletir.

    ``__main__.lifespan`` tarafından ``asyncio.create_task`` ile başlatılır;
    ``CancelledError`` ile temiz çıkış. Hatalar yutulup loglanır (engine'lerle
    aynı dayanıklılık deseni).
    """
    await logger.ainfo(
        "Push dispatch loop başlatıldı",
        interval_seconds=interval_seconds,
    )
    try:
        while True:
            try:
                sent = await dispatch_once(db)
                if sent > 0:
                    await logger.adebug("Threshold alarm push dispatch", count=sent)
            except Exception:
                await logger.aerror("Push dispatch cycle'ında hata", exc_info=True)
            try:
                await asyncio.sleep(interval_seconds)
            except asyncio.CancelledError:
                break
    finally:
        await logger.ainfo("Push dispatch loop durdu")
