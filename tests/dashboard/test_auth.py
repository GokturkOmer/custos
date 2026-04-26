"""V11-101 Auth + 2 rol testleri.

Üç katmanda test:

1. **Shared/auth primitif'leri** (DB'siz) — bcrypt + token üretimi
2. **Auth flow** (TestClient + DB) — login, logout, redirect, must_change
3. **Rol kontrolü** (TestClient + DB) — operator vs developer 403/200

DB gerektiren testler ``pytest.skip`` ile graceful — CI'da DB yoksa skip,
DB varsa full flow doğrulanır. Bu örüntü mevcut dashboard testleriyle
uyumlu (ör. ``test_routes.py`` 503 ↔ 200 ayrımı).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from custos.__main__ import app
from custos.shared.auth import (
    SESSION_COOKIE_NAME,
    SESSION_TTL_SECONDS,
    create_session_token,
    hash_password,
    verify_password,
)
from custos.shared.database import DatabaseInterface


def _client() -> TestClient:
    """Test'e özel TestClient — conftest'in autouse override'ı bu modülü atlar."""
    return TestClient(app)


def _get_db() -> DatabaseInterface | None:
    """App state'inden DB instance'ını al — yoksa None döner (skip)."""
    db: DatabaseInterface | None = getattr(app.state, "db", None)
    return db


# --- 1. Shared/auth primitif testleri (DB'siz) ---


def test_hash_password_then_verify_succeeds() -> None:
    """Düz parola hash'lenince verify True döner."""
    hashed = hash_password("ParolaA1!")
    assert hashed != "ParolaA1!"
    assert hashed.startswith("$2b$") or hashed.startswith("$2a$")
    assert verify_password("ParolaA1!", hashed) is True


def test_verify_password_wrong_returns_false() -> None:
    """Yanlış parola False döner — exception fırlatmaz."""
    hashed = hash_password("DogruParola")
    assert verify_password("YanlisParola", hashed) is False


def test_verify_password_invalid_hash_returns_false() -> None:
    """Bozuk hash → False, exception yok."""
    assert verify_password("herhangi", "bozuk-hash-formati") is False


def test_create_session_token_returns_unique_url_safe() -> None:
    """Token URL-safe karakterler içermeli, çakışma olasılığı sıfır."""
    tokens = {create_session_token() for _ in range(50)}
    assert len(tokens) == 50  # hepsi farklı
    for tok in tokens:
        assert len(tok) >= 32
        # base64 URL-safe: [A-Za-z0-9_-]
        assert all(c.isalnum() or c in "_-" for c in tok)


# --- 2. Auth flow testleri (TestClient + DB) ---


def test_protected_route_without_session_redirects_to_login() -> None:
    """Cookie yoksa korumalı route /login'e redirect."""
    client = _client()
    response = client.get("/dashboard/overview", follow_redirects=False)
    if response.status_code == 503:
        pytest.skip("DB yok — auth dependency 503 dönüyor")
    assert response.status_code == 303
    assert response.headers.get("location") == "/login"


def test_login_with_wrong_password_returns_303_with_error() -> None:
    """Hatalı kimlikle login formu hata mesajıyla geri döner."""
    client = _client()
    response = client.post(
        "/login",
        data={"username": "nonexistent_user_xyz", "password": "wrong"},
        follow_redirects=False,
    )
    if response.status_code == 503:
        pytest.skip("DB yok")
    assert response.status_code == 303
    assert "/login?error=invalid" in response.headers.get("location", "")


def test_login_with_valid_credentials_sets_cookie() -> None:
    """Geçerli kimlikle login → 303 redirect + custos_session cookie."""
    db = _get_db()
    if db is None:
        pytest.skip("DB yok")

    import asyncio

    username = f"test_login_{int(datetime.now(UTC).timestamp())}"
    password = "TestParola123!"

    async def _setup() -> int:
        existing = await db.get_user_by_username(username)
        if existing:
            return existing.id
        u = await db.create_user(
            username=username,
            password_hash=hash_password(password),
            role="developer",
            must_change_password=False,
        )
        return u.id

    user_id = asyncio.run(_setup())
    try:
        client = _client()
        response = client.post(
            "/login",
            data={"username": username, "password": password},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert SESSION_COOKIE_NAME in response.cookies
    finally:
        # Cleanup — set_user_enabled(False) yerine direkt sil mümkün değil
        # (delete_user metodu yok); bu paketin scope dışı, devre dışı bırak.
        asyncio.run(db.set_user_enabled(user_id, False))


def test_logout_clears_session_cookie() -> None:
    """Logout cookie'yi temizleyip /login'e yönlendirir."""
    client = _client()
    response = client.post("/logout", follow_redirects=False)
    if response.status_code == 503:
        pytest.skip("DB yok")
    assert response.status_code == 303
    assert response.headers.get("location") == "/login"
    # Set-Cookie içinde max-age=0 veya expires=past
    set_cookie = response.headers.get("set-cookie", "")
    assert SESSION_COOKIE_NAME in set_cookie


# --- 3. Rol kontrolü testleri ---


def test_operator_cannot_delete_threshold() -> None:
    """Operator rol developer-only route'a erişemez (403)."""
    db = _get_db()
    if db is None:
        pytest.skip("DB yok")

    import asyncio

    op_username = f"test_op_{int(datetime.now(UTC).timestamp())}"
    op_password = "OperatorParola1!"

    async def _setup() -> tuple[int, str]:
        u = await db.create_user(
            username=op_username,
            password_hash=hash_password(op_password),
            role="operator",
            must_change_password=False,
        )
        token = create_session_token()
        expires = datetime.now(UTC) + timedelta(seconds=SESSION_TTL_SECONDS)
        await db.create_session(u.id, token, expires)
        return u.id, token

    user_id, token = asyncio.run(_setup())
    try:
        client = _client()
        client.cookies.set(SESSION_COOKIE_NAME, token)
        # /thresholds/999/delete → developer-only POST
        response = client.post(
            "/dashboard/thresholds/999/delete",
            follow_redirects=False,
        )
        assert response.status_code == 403
    finally:
        asyncio.run(db.delete_session(token))
        asyncio.run(db.set_user_enabled(user_id, False))


def test_developer_can_access_logs() -> None:
    """Developer rol audit log sayfasına 200 ile erişir."""
    db = _get_db()
    if db is None:
        pytest.skip("DB yok")

    import asyncio

    dev_username = f"test_dev_{int(datetime.now(UTC).timestamp())}"
    dev_password = "DevParola1!"

    async def _setup() -> tuple[int, str]:
        u = await db.create_user(
            username=dev_username,
            password_hash=hash_password(dev_password),
            role="developer",
            must_change_password=False,
        )
        token = create_session_token()
        expires = datetime.now(UTC) + timedelta(seconds=SESSION_TTL_SECONDS)
        await db.create_session(u.id, token, expires)
        return u.id, token

    user_id, token = asyncio.run(_setup())
    try:
        client = _client()
        client.cookies.set(SESSION_COOKIE_NAME, token)
        response = client.get("/dashboard/logs")
        assert response.status_code == 200
    finally:
        asyncio.run(db.delete_session(token))
        asyncio.run(db.set_user_enabled(user_id, False))


def test_must_change_password_redirects_other_routes() -> None:
    """``must_change_password=True`` kullanıcı /change-password'e yönlendirilir."""
    db = _get_db()
    if db is None:
        pytest.skip("DB yok")

    import asyncio

    pw_username = f"test_mustpw_{int(datetime.now(UTC).timestamp())}"

    async def _setup() -> tuple[int, str]:
        u = await db.create_user(
            username=pw_username,
            password_hash=hash_password("ParolaInit1!"),
            role="operator",
            must_change_password=True,
        )
        token = create_session_token()
        expires = datetime.now(UTC) + timedelta(seconds=SESSION_TTL_SECONDS)
        await db.create_session(u.id, token, expires)
        return u.id, token

    user_id, token = asyncio.run(_setup())
    try:
        client = _client()
        client.cookies.set(SESSION_COOKIE_NAME, token)
        response = client.get("/dashboard/overview", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers.get("location") == "/change-password"
    finally:
        asyncio.run(db.delete_session(token))
        asyncio.run(db.set_user_enabled(user_id, False))


def test_session_cleanup_removes_expired() -> None:
    """``cleanup_expired_sessions`` süresi geçen kayıtları siler."""
    db = _get_db()
    if db is None:
        pytest.skip("DB yok")

    import asyncio

    cleanup_username = f"test_cleanup_{int(datetime.now(UTC).timestamp())}"

    async def _setup_and_cleanup() -> tuple[bool, int]:
        u = await db.create_user(
            username=cleanup_username,
            password_hash=hash_password("CleanupParola1!"),
            role="operator",
            must_change_password=False,
        )
        # Süresi GEÇMİŞ session
        old_token = create_session_token()
        past_expiry = datetime.now(UTC) - timedelta(hours=1)
        await db.create_session(u.id, old_token, past_expiry)

        deleted_count = await db.cleanup_expired_sessions()
        # Eski token artık çekilemez
        leftover = await db.get_session_by_token(old_token)
        return leftover is None, deleted_count

    cleaned, deleted = asyncio.run(_setup_and_cleanup())
    assert cleaned is True
    assert deleted >= 1


def test_audit_log_records_login_and_logout() -> None:
    """Login + logout audit log'a yazılır."""
    db = _get_db()
    if db is None:
        pytest.skip("DB yok")

    import asyncio

    audit_username = f"test_audit_{int(datetime.now(UTC).timestamp())}"
    audit_password = "AuditParola1!"

    async def _setup_user() -> int:
        u = await db.create_user(
            username=audit_username,
            password_hash=hash_password(audit_password),
            role="developer",
            must_change_password=False,
        )
        return u.id

    user_id = asyncio.run(_setup_user())
    try:
        client = _client()
        login = client.post(
            "/login",
            data={"username": audit_username, "password": audit_password},
            follow_redirects=False,
        )
        assert login.status_code == 303
        client.post("/logout", follow_redirects=False)

        async def _check_audit() -> tuple[bool, bool]:
            entries = await db.list_audit_log(category="auth", limit=20)
            user_id_str = str(user_id)
            has_login = any(e.action == "login" and e.entity_id == user_id_str for e in entries)
            has_logout = any(e.action == "logout" and e.entity_id == user_id_str for e in entries)
            return has_login, has_logout

        login_recorded, logout_recorded = asyncio.run(_check_audit())
        assert login_recorded is True
        assert logout_recorded is True
    finally:
        asyncio.run(db.set_user_enabled(user_id, False))
