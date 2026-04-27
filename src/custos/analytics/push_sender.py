"""Web Push bildirim gönderici.

Alarm tetiklendiğinde aktif abonelere push bildirim gönderir.
Sessiz saat ve severity filtresi uygular.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

import structlog
from pywebpush import WebPushException, webpush

from custos.shared.config import settings
from custos.shared.database import DatabaseInterface, PushSubscription
from custos.shared.vapid import get_vapid_keys, get_vapid_mailto, is_push_enabled

logger = structlog.get_logger(logger_name="push_sender")


def _is_quiet_hour(sub: PushSubscription, now_time: time) -> bool:
    """Aboneliğin sessiz saat aralığında olup olmadığını kontrol eder."""
    if sub.quiet_start is None or sub.quiet_end is None:
        return False

    start = sub.quiet_start
    end = sub.quiet_end

    if start <= end:
        # Normal aralık (ör: 08:00 - 18:00)
        return start <= now_time <= end
    # Gece yarısını geçen aralık (ör: 22:00 - 07:00)
    return now_time >= start or now_time <= end


def _should_notify(sub: PushSubscription, severity: str, now_time: time) -> bool:
    """Aboneliğe bildirim gönderilmeli mi kontrol eder.

    4-tier severity (V11-107/K10):
    - ``emergency`` : Sessiz saat ve abonelik filtresi BYPASS — her zaman
      gönderilir (insan hayatı/operasyon riski).
    - ``crit``      : ``notify_crit`` boolean'ına bağlı.
    - ``warn``      : ``notify_warn`` boolean'ına bağlı.
    - ``info``      : Abonelikte ayrı kolon yok (P-03 eklenecek);
      şimdilik ``notify_warn`` fallback (info zaten "haberdar et" tier'i).

    Sessiz saat kuralı emergency haricinde uygulanır.
    """
    # Emergency: tüm filtreler bypass — gönder.
    if severity == "emergency":
        return True

    # Sessiz saat kontrolü (emergency dışındaki tüm tier'ler)
    if _is_quiet_hour(sub, now_time):
        return False

    # Severity filtresi
    if severity == "crit":
        return sub.notify_crit
    if severity == "warn":
        return sub.notify_warn
    if severity == "info":
        # P-03'te ``notify_info`` kolonu eklenince bu fallback kalkar.
        return sub.notify_warn

    # Bilinmeyen severity — varsayılan olarak gönderme (constraint
    # sayesinde 4 değer dışı gelmemeli, defansif).
    return False


async def send_push_notifications(
    db: DatabaseInterface,
    title: str,
    body: str,
    severity: str,
) -> int:
    """Aktif abonelere push bildirim gönderir.

    Sessiz saat ve severity filtresi uygular.
    Gönderilen bildirim sayısını döndürür.
    """
    if not is_push_enabled():
        await logger.adebug("Push bildirim devre dışı — VAPID anahtarları yapılandırılmamış")
        return 0

    public_key, private_key = get_vapid_keys()
    mailto = get_vapid_mailto()

    subs = await db.list_push_subscriptions()
    if not subs:
        return 0

    # Sessiz saat karsilastirmasi kullanicinin yerel saatinde yapilmali
    local_tz = ZoneInfo(settings.custos_timezone)
    now_time = datetime.now(UTC).astimezone(local_tz).time()
    is_emergency = severity == "emergency"
    payload = json.dumps(
        {
            "title": title,
            "body": body,
            "tag": f"custos-{severity}",
            "url": "/dashboard/alarms",
            # Emergency'de browser yüksek öncelik gösterimi — service worker
            # bu flag'i okuyup vibrate + requireInteraction uygulayabilir.
            "priority": "high" if is_emergency else "normal",
        }
    )

    sent = 0
    for sub in subs:
        if not _should_notify(sub, severity, now_time):
            continue

        try:
            webpush(
                subscription_info={
                    "endpoint": sub.endpoint,
                    "keys": {
                        "p256dh": sub.p256dh,
                        "auth": sub.auth,
                    },
                },
                data=payload,
                vapid_private_key=private_key,
                vapid_claims={
                    "sub": mailto,
                    "aud": _extract_origin(sub.endpoint),
                },
            )
            sent += 1
        except WebPushException as exc:
            if hasattr(exc, "response") and exc.response is not None:
                status_code = exc.response.status_code
                if status_code == 410:
                    # 410 Gone — abonelik artık geçersiz, sil
                    await db.delete_push_subscription(sub.endpoint)
                    await logger.ainfo(
                        "Geçersiz push aboneliği silindi (410 Gone)",
                        endpoint=sub.endpoint[:50],
                    )
                    continue
            await logger.awarning(
                "Push bildirim gönderilemedi",
                endpoint=sub.endpoint[:50],
                error=str(exc),
            )
        except Exception:
            await logger.awarning(
                "Push bildirim gönderiminde beklenmeyen hata",
                endpoint=sub.endpoint[:50],
                exc_info=True,
            )

    if sent > 0:
        await logger.ainfo(
            "Push bildirimler gönderildi",
            sent=sent,
            total_subs=len(subs),
            severity=severity,
        )

    return sent


def _extract_origin(endpoint: str) -> str:
    """Endpoint URL'sinden origin kısmını çıkarır."""
    # https://fcm.googleapis.com/fcm/send/xxx → https://fcm.googleapis.com
    from urllib.parse import urlparse

    parsed = urlparse(endpoint)
    return f"{parsed.scheme}://{parsed.netloc}"
