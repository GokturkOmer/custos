"""Faz 0 Bölüm 2 — analytics `GET /auth/validate` forward_auth ucu testleri (0.6).

Caddy `/assistant/*` isteğini asistan servisine geçirmeden önce bu ucu
forward_auth ile çağırır. İki test katmanı:

1. **Header round-trip (DB'siz):** analytics ``encode_custos_user_header`` →
   asistan ``parse_custos_user_header`` BİREBİR uyumlu mu (Türkçe kullanıcı adı
   dahil) — iki süreç arasındaki sözleşme.
2. **Endpoint davranışı (DB'siz, dependency_override):** ``get_current_session``
   override edilerek gerçek ``require_operator`` zinciri koşar:
   - geçerli operator → 200 + doğru `X-Custos-User` header
   - geçerli developer → 200 (require_operator developer'ı da kapsar)
   - session yok → 303 /login (header üretilmez)
   - operator/developer dışı rol → 403 (header üretilmez)

``get_current_session`` override'ı DB'ye gitmeden session enjekte eder; testler
DB olmadan da kararlı koşar. Dosya adı "test_auth" içerdiği için dashboard
conftest'inin auth-bypass'ı UYGULANMAZ (gerçek dependency'ler koşar).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from custos.__main__ import app
from custos.analytics.dashboard.auth_dependencies import get_current_session
from custos.analytics.dashboard.auth_routes import (
    CUSTOS_USER_HEADER,
    encode_custos_user_header,
)
from custos.assistant.middleware import parse_custos_user_header
from custos.shared.database import Session

_FAR_FUTURE = datetime(2099, 1, 1, tzinfo=UTC)


def _session(*, role: str, user_id: int = 42, username: str = "şahin") -> Session:
    """Test için sahte Session — gerçek require_operator zinciri bunu görür.

    ``id`` (session satırı) bilerek ``user_id``'den FARKLI verilir; header'ın
    ``user_id``'yi taşıdığını (session satır id'sini DEĞİL) doğrulamak için.
    """
    return Session(
        id=1,
        user_id=user_id,
        username=username,
        role=role,
        enabled=True,
        must_change_password=False,
        expires_at=_FAR_FUTURE,
    )


@contextmanager
def _with_session(value: Session | None) -> Iterator[TestClient]:
    """``get_current_session`` override'lı TestClient; çıkışta override temizlenir."""
    app.dependency_overrides[get_current_session] = lambda: value
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_current_session, None)


# --- 1. Header round-trip (DB'siz sözleşme testi) ---


def test_header_round_trip_with_turkish_username() -> None:
    """analytics encode → asistan middleware decode BİREBİR (Türkçe ad dahil)."""
    raw = encode_custos_user_header(
        user_id=7, username="şükrü öztürk", role="operator"
    )
    # base64url çıktısı header'a güvenle konacak şekilde tamamen ASCII olmalı.
    assert raw.isascii()
    user = parse_custos_user_header(raw)
    assert user is not None
    assert user.id == 7
    assert user.username == "şükrü öztürk"
    assert user.role == "operator"


# --- 2. Endpoint davranışı (DB'siz, get_current_session override) ---


def test_validate_valid_operator_returns_200_with_header() -> None:
    """Geçerli operator session → 200 + decode edilebilir X-Custos-User header."""
    with _with_session(_session(role="operator", user_id=42, username="çağrı")) as c:
        resp = c.get("/auth/validate", follow_redirects=False)
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    header = resp.headers.get(CUSTOS_USER_HEADER)
    assert header is not None
    user = parse_custos_user_header(header)
    assert user is not None
    # Header KULLANICI kimliğini (user_id=42) taşır, session satır id'sini (1) değil.
    assert user.id == 42
    assert user.username == "çağrı"
    assert user.role == "operator"


def test_validate_developer_also_allowed() -> None:
    """developer rolü de bu uçtan geçer (require_operator developer'ı kapsar)."""
    with _with_session(_session(role="developer")) as c:
        resp = c.get("/auth/validate", follow_redirects=False)
    assert resp.status_code == 200
    user = parse_custos_user_header(resp.headers.get(CUSTOS_USER_HEADER))
    assert user is not None
    assert user.role == "developer"


def test_validate_no_session_redirects_303() -> None:
    """Session yok → require_operator 303 /login; X-Custos-User üretilmez."""
    with _with_session(None) as c:
        resp = c.get("/auth/validate", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers.get("location") == "/login"
    assert CUSTOS_USER_HEADER not in resp.headers


def test_validate_wrong_role_returns_403() -> None:
    """operator/developer dışı rol → 403; X-Custos-User üretilmez."""
    with _with_session(_session(role="viewer")) as c:
        resp = c.get("/auth/validate", follow_redirects=False)
    assert resp.status_code == 403
    assert CUSTOS_USER_HEADER not in resp.headers
