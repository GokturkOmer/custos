"""`X-Custos-User` header parse testleri (karar A).

Saf fonksiyon testi — HTTP/DB yok. Geçerli base64url(JSON) çözümü, Türkçe
(non-ASCII) kullanıcı adı round-trip'i ve tüm bozuk/eksik girdilerde güvenli
`None` davranışı doğrulanır.
"""

from __future__ import annotations

import base64
import json

from custos.assistant.middleware import AssistantUser, parse_custos_user_header


def _encode(payload: object) -> str:
    """payload'ı base64url(JSON) header değerine çevirir (padding'siz)."""
    raw = json.dumps(payload).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def test_gecerli_header_parse_edilir() -> None:
    """id/username/role içeren geçerli header → AssistantUser."""
    header = _encode({"id": 42, "username": "gokturk", "role": "developer"})
    user = parse_custos_user_header(header)
    assert user == AssistantUser(id=42, username="gokturk", role="developer")


def test_turkce_kullanici_adi_round_trip() -> None:
    """Non-ASCII kullanıcı adı (base64url'in asıl gerekçesi) korunur."""
    header = _encode({"id": 1, "username": "Gökçe Şahin", "role": "operator"})
    user = parse_custos_user_header(header)
    assert user is not None
    assert user.username == "Gökçe Şahin"


def test_padding_eksik_header_de_cozulur() -> None:
    """Analytics padding'siz base64url yollarsa da çözülmeli."""
    header = _encode({"id": 3, "username": "ab", "role": "operator"})
    assert "=" not in header  # _encode padding'i kırpar
    assert parse_custos_user_header(header) is not None


def test_header_yoksa_none() -> None:
    """Header None veya boş → None (dev/Caddy'siz durum)."""
    assert parse_custos_user_header(None) is None
    assert parse_custos_user_header("") is None


def test_bozuk_base64_none() -> None:
    """Geçersiz base64 → None (exception sızdırmaz)."""
    assert parse_custos_user_header("!!!bu-base64-degil!!!") is None


def test_base64_ama_json_degil_none() -> None:
    """Geçerli base64, geçersiz JSON → None."""
    not_json = base64.urlsafe_b64encode(b"duz metin, json degil").decode("ascii").rstrip("=")
    assert parse_custos_user_header(not_json) is None


def test_json_dict_degil_none() -> None:
    """JSON liste (dict değil) → None."""
    assert parse_custos_user_header(_encode([1, 2, 3])) is None


def test_eksik_alan_none() -> None:
    """role eksik → None."""
    assert parse_custos_user_header(_encode({"id": 1, "username": "x"})) is None


def test_yanlis_tip_none() -> None:
    """id string ise → None."""
    header = _encode({"id": "1", "username": "x", "role": "operator"})
    assert parse_custos_user_header(header) is None


def test_id_bool_reddedilir() -> None:
    """bool int alt tipidir; id=True gibi değerler reddedilmeli."""
    header = _encode({"id": True, "username": "x", "role": "operator"})
    assert parse_custos_user_header(header) is None
