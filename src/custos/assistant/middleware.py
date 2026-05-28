"""`X-Custos-User` forward_auth header parse'ı (karar A).

Caddy `/assistant/*` isteğini geçirmeden önce analytics'e sorar (forward_auth →
`GET /auth/validate`, `require_operator`). Geçerli operatör session'ında analytics
200 + `X-Custos-User` header döner:

    X-Custos-User: base64url(JSON {"id": int, "username": str, "role": str})

base64url ZORUNLU: Türkçe kullanıcı adları non-ASCII (ş/ğ/ı/ö/ü/ç) içerir; HTTP
header değerleri ise pratikte ASCII/latin-1 ile sınırlı. base64url ham JSON'u
güvenle ASCII'ye taşır.

Asistan servisi BAŞKA auth kodu içermez — yetki Caddy + analytics tarafında
garanti edilir. Bu modül yalnızca header'ı çözüp `request.state.user`'a yazar
(yoksa veya bozuksa `None`). Caddy/forward_auth wiring Bölüm 2'de; dev'de Caddy
olmadan header hiç gelmez → `request.state.user is None` (servis yine çalışır).
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass

import structlog

logger = structlog.get_logger(logger_name="assistant.middleware")

# Caddy forward_auth'un ürettiği header adı (analytics tarafıyla sözleşme).
CUSTOS_USER_HEADER = "X-Custos-User"


@dataclass(frozen=True)
class AssistantUser:
    """`X-Custos-User`'dan çözülen oturum kullanıcısı (yalnız okuma)."""

    id: int
    username: str
    role: str


def parse_custos_user_header(raw: str | None) -> AssistantUser | None:
    """`X-Custos-User` header değerini `AssistantUser`'a çevirir.

    base64url(JSON) çözer; eksik padding tolere edilir. Header yok, boş, bozuk
    base64, geçersiz JSON veya beklenen alanlar (id:int, username:str, role:str)
    eksik/yanlış tipteyse `None` döner — asla exception sızdırmaz (saldırgan
    bozuk header ile servisi düşürememeli).
    """
    if not raw:
        return None
    try:
        # base64url padding'i 4'ün katına tamamla (analytics padding'siz yollarsa).
        padded = raw + "=" * (-len(raw) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
        data = json.loads(decoded.decode("utf-8"))
    except (binascii.Error, ValueError, UnicodeDecodeError):
        logger.warning("custos_user_header_cozulemedi")
        return None

    if not isinstance(data, dict):
        return None
    user_id = data.get("id")
    username = data.get("username")
    role = data.get("role")
    # bool, int'in alt tipi — id'nin gerçek int olmasını şart koş (True/False reddet).
    if (
        not isinstance(user_id, int)
        or isinstance(user_id, bool)
        or not isinstance(username, str)
        or not isinstance(role, str)
    ):
        logger.warning("custos_user_header_alanlari_gecersiz")
        return None
    return AssistantUser(id=user_id, username=username, role=role)
