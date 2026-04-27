"""Web Push bildirim gönderici.

Alarm tetiklendiğinde aktif abonelere push bildirim gönderir.
Master switch + sessiz saat + severity filtresi uygular.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, time
from urllib.parse import urlparse
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

    4-tier severity (V11-107/K10) + P-03 abonelik tercihleri:

    - ``enabled=False``        : Cihaz tek-tıkla susturulmuş — hiçbir tier gitmez.
    - ``emergency``            : ``notify_emergency`` aktif ise sessiz saat
      bypass (insan hayatı/operasyon riski). Kullanıcı bu cihazda emergency
      almayı kapatmışsa (notify_emergency=False) gitmez.
    - ``crit`` / ``warn`` / ``info``: Sessiz saat içindeyse atlanır; ardından
      ilgili ``notify_<tier>`` boolean'ına bakılır.
    """
    # Cihaz susturulduysa hiçbir tier gitmez (master switch sender içinde).
    if not sub.enabled:
        return False

    # Emergency: sessiz saat bypass, ama abonelik tercihi geçerli.
    if severity == "emergency":
        return sub.notify_emergency

    # Sessiz saat kontrolü (emergency dışındaki tüm tier'ler)
    if _is_quiet_hour(sub, now_time):
        return False

    # Severity filtresi — her tier kendi kolonunda.
    if severity == "crit":
        return sub.notify_crit
    if severity == "warn":
        return sub.notify_warn
    if severity == "info":
        return sub.notify_info

    # Bilinmeyen severity — varsayılan olarak gönderme (constraint
    # sayesinde 4 değer dışı gelmemeli, defansif).
    return False


def _build_payload(title: str, body: str, severity: str) -> str:
    """Web Push payload JSON'unu üretir.

    Service worker emergency'de ``priority='high'`` flag'ini okuyup
    vibrate + requireInteraction uygular.
    """
    return json.dumps(
        {
            "title": title,
            "body": body,
            "tag": f"custos-{severity}",
            "url": "/dashboard/alarms",
            "priority": "high" if severity == "emergency" else "normal",
        }
    )


async def _send_one_push(
    sub: PushSubscription,
    payload: str,
    private_key: str,
    mailto: str,
    db: DatabaseInterface,
) -> bool:
    """Tek bir aboneliğe push gönderir. 410 Gone'da abonelik silinir.

    Başarılı gönderim için ``True``; hata (geçersiz / aktarım) durumlarında
    ``False`` döner. Beklenmeyen exception'ları log'a yazar ama yutar —
    bir aboneliğin başarısızlığı diğerlerini etkilemesin.
    """
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
        return True
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
                return False
        await logger.awarning(
            "Push bildirim gönderilemedi",
            endpoint=sub.endpoint[:50],
            error=str(exc),
        )
        return False
    except Exception:
        await logger.awarning(
            "Push bildirim gönderiminde beklenmeyen hata",
            endpoint=sub.endpoint[:50],
            exc_info=True,
        )
        return False


async def send_push_notifications(
    db: DatabaseInterface,
    title: str,
    body: str,
    severity: str,
    is_test: bool = False,
) -> int:
    """Aktif abonelere push bildirim gönderir.

    Master switch (``retention_config.push_global_enabled=False``) → erken
    dönüş, hiçbir cihaza gitmez (her abonelik ``enabled`` flag'inden
    bağımsız). Tatil/eğitim sırasında tek anahtarla susturma için (P-03).

    ``is_test=True`` (P-04): bakım modunda üretilen alarm'lar için çağıran
    bu flag'i geçirir; sender erken-dönüş yapar (alarm DB'de is_test=true
    olarak kayıtlı, sadece push iletisi atlanır). Audit log
    threshold_engine tarafında.

    Sessiz saat ve severity filtresi her abonelik için ``_should_notify``
    içinde uygulanır. Gönderilen bildirim sayısını döndürür.
    """
    # P-04: bakım modu alarm'ı — push gitmez. Master switch'ten önce
    # kontrol etmek mantıklı (DB get_retention_config çağrısından da
    # tasarruf — pratikte ihmal edilebilir ama net kalsın).
    if is_test:
        await logger.ainfo(
            "Bakım modu alarm'ı (is_test=True) — push atlandı",
            severity=severity,
        )
        return 0

    if not is_push_enabled():
        await logger.adebug("Push bildirim devre dışı — VAPID anahtarları yapılandırılmamış")
        return 0

    # Master switch — runtime tablosundan oku. Pratikte her alarm push'unda
    # DB hit, ama push'lar seyrek (alarm sırasında) ve abonelik sayısı az,
    # cache eklemeye gerek yok.
    retention = await db.get_retention_config()
    if not retention.push_global_enabled:
        await logger.ainfo(
            "Push global devre dışı (master switch) — bildirim atlandı",
            severity=severity,
        )
        return 0

    _public_key, private_key = get_vapid_keys()
    mailto = get_vapid_mailto()

    subs = await db.list_push_subscriptions()
    if not subs:
        return 0

    # Sessiz saat karsilastirmasi kullanicinin yerel saatinde yapilmali
    local_tz = ZoneInfo(settings.custos_timezone)
    now_time = datetime.now(UTC).astimezone(local_tz).time()
    payload = _build_payload(title, body, severity)

    sent = 0
    for sub in subs:
        if not _should_notify(sub, severity, now_time):
            continue
        if await _send_one_push(sub, payload, private_key, mailto, db):
            sent += 1

    if sent > 0:
        await logger.ainfo(
            "Push bildirimler gönderildi",
            sent=sent,
            total_subs=len(subs),
            severity=severity,
        )

    return sent


async def send_push_to_subscription(
    db: DatabaseInterface,
    sub: PushSubscription,
    title: str,
    body: str,
    severity: str = "warn",
) -> int:
    """Tek bir aboneliğe test push gönderir.

    Settings UI'daki "test bildirimi gönder" butonu için. Master switch ve
    filtreler BYPASS — kullanıcı cihazında push'un çalıştığını doğrulayabilsin
    (tatildeyken bile cihazını test edebilmeli).

    Başarı için 1, başarısız/VAPID yok için 0 döner.
    """
    if not is_push_enabled():
        return 0
    _public_key, private_key = get_vapid_keys()
    mailto = get_vapid_mailto()
    payload = _build_payload(title, body, severity)
    return 1 if await _send_one_push(sub, payload, private_key, mailto, db) else 0


def _extract_origin(endpoint: str) -> str:
    """Endpoint URL'sinden origin kısmını çıkarır."""
    # https://fcm.googleapis.com/fcm/send/xxx → https://fcm.googleapis.com
    parsed = urlparse(endpoint)
    return f"{parsed.scheme}://{parsed.netloc}"
