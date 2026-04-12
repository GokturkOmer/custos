"""VAPID key yönetimi.

Web Push bildirimleri için VAPID (Voluntary Application Server
Identification) anahtarlarını settings'ten okur.
"""

from __future__ import annotations

from custos.shared.config import settings


def get_vapid_keys() -> tuple[str, str]:
    """VAPID public ve private key'leri settings'ten döndürür.

    Yoksa boş string döner (bildirim devre dışı).
    Returns:
        (public_key, private_key) tuple'ı
    """
    return settings.custos_vapid_public_key, settings.custos_vapid_private_key


def get_vapid_mailto() -> str:
    """VAPID mailto adresini döndürür."""
    return settings.custos_vapid_mailto


def is_push_enabled() -> bool:
    """VAPID anahtarları yapılandırılmış mı kontrol eder."""
    pub, priv = get_vapid_keys()
    return bool(pub and priv)
