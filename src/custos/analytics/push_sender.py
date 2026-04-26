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
    """Aboneliğe bildirim gönderilmeli mi kontrol eder."""
    # Sessiz saat kontrolü
    if _is_quiet_hour(sub, now_time):
        return False

    # Severity filtresi
    if severity == "warn" and not sub.notify_warn:
        return False
    if severity == "crit" and not sub.notify_crit:
        return False

    return True


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
    payload = json.dumps(
        {
            "title": title,
            "body": body,
            "tag": f"custos-{severity}",
            "url": "/dashboard/alarms",
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
