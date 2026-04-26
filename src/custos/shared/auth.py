"""Auth yardımcıları — bcrypt parola hash + secure session token üretimi.

V11-101 (v1.1 Paket 01): dashboard'a oturum + 2 rol (operator/developer)
desteği eklenir. Bu modül framework-bağımsız primitif'leri içerir; FastAPI
dependency'leri ``analytics/dashboard/auth_dependencies.py`` içindedir.

Pratik notlar:
- bcrypt 12 round (~250 ms hash, brute force makul caydırıcı). Pilot mini PC
  CPU'sunda login akışı için kabul edilebilir.
- Session token ``secrets.token_urlsafe(32)`` → 43 char URL-safe; cookie
  içinde saklanır (``custos_session``), DB'de UNIQUE indeks.
- Session süresi: 12 saat (LAN trust + uzun çalışma günü). Auto-renewal yok.
"""

from __future__ import annotations

import secrets

import bcrypt

# Bcrypt cost factor — 12 round endüstri default. Mini PC CPU'sunda yaklaşık
# 250-400 ms; saha pilot için kabul edilebilir. 14+ round login UX'ini bozar.
_BCRYPT_ROUNDS = 12

# Session cookie ismi — login sonrası HttpOnly cookie olarak set edilir.
SESSION_COOKIE_NAME = "custos_session"

# Session TTL — 12 saat (LAN trust). Auto-renewal yok; süre dolunca login
# sayfasına yönlendirilir.
SESSION_TTL_SECONDS = 12 * 3600


def hash_password(plain: str) -> str:
    """Düz parolayı bcrypt ile hash'ler.

    bcrypt 12 round; salt otomatik üretilir. Sonuç UTF-8 string olarak
    döndürülür (DB'de TEXT kolonu).
    """
    if not plain:
        msg = "parola boş olamaz"
        raise ValueError(msg)
    salt = bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)
    hashed: bytes = bcrypt.hashpw(plain.encode("utf-8"), salt)
    return hashed.decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Düz parolayı hash'le karşılaştırır.

    bcrypt sabit-zaman karşılaştırma yapar — timing attack güvenli. Boş veya
    bozuk hash girdisinde ``False`` döner (exception fırlatmaz).
    """
    if not plain or not hashed:
        return False
    try:
        result: bool = bcrypt.checkpw(
            plain.encode("utf-8"), hashed.encode("utf-8"),
        )
    except (ValueError, TypeError):
        # Hash formatı bozuksa (eksik salt prefix vs.) sessizce False —
        # login sayfasında "kullanıcı adı veya parola hatalı" gösterilir.
        return False
    return result


def create_session_token() -> str:
    """Cryptographically secure session token üretir.

    ``secrets.token_urlsafe(32)`` 32 byte rastgele veriden 43 karakter
    URL-safe base64 string üretir. Çakışma olasılığı pratik olarak sıfır;
    DB'de UNIQUE indeks çakışmayı yine de yakalar.
    """
    return secrets.token_urlsafe(32)
