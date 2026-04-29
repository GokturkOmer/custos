"""PP-06 — IP-bazlı login brute-force koruması testi.

audit_log'taki son LOGIN_RATE_LIMIT_WINDOW_MINUTES içindeki
'login_failed' kayıtları sayılır; eşik LOGIN_RATE_LIMIT_MAX_ATTEMPTS
aşıldığında 'rate_limited' error ile reddedilir.

H-2 (29 Nis 2026 denetim) eki: username-bazlı sayım — dağıtık brute-force
(her istek farklı IP'den) bypass'ı kapatır. Aynı username için eşik
``LOGIN_RATE_LIMIT_USERNAME_MAX_ATTEMPTS``.

DB gerektirir; yoksa skip.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from custos.__main__ import app
from custos.analytics.dashboard.auth_routes import (
    LOGIN_RATE_LIMIT_MAX_ATTEMPTS,
)
from custos.shared.database import AuditLogEntry, DatabaseInterface


def _client() -> TestClient:
    """test_auth.py ile aynı pattern."""
    return TestClient(app)


def _get_db() -> DatabaseInterface | None:
    db: DatabaseInterface | None = getattr(app.state, "db", None)
    return db


async def _purge_test_ip_failed_logins(db: DatabaseInterface) -> None:
    """Audit_log'taki 'testclient' IP login_failed satırlarını siler.

    TestClient sabit IP ('testclient') gönderir; başka testler de
    aynı IP'den login_failed yazınca pencere içinde sayım birikiyor.
    Her test öncesi/sonrası bu izi temizliyoruz.
    """
    pool: Any = cast(Any, db)._get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM audit_log WHERE category='auth' "
            "AND action='login_failed' AND detail LIKE 'ip=testclient %'"
        )


def test_rate_limit_blocks_after_threshold() -> None:
    """LOGIN_RATE_LIMIT_MAX_ATTEMPTS yanlış denemeden sonra rate_limited."""
    db = _get_db()
    if db is None:
        pytest.skip("DB yok")

    asyncio.run(_purge_test_ip_failed_logins(db))

    client = _client()
    bad_user = f"TEST_ratelimit_{int(datetime.now(UTC).timestamp())}"

    try:
        # Eşik kadar başarısız deneme — hepsi 'invalid' error döner
        for _ in range(LOGIN_RATE_LIMIT_MAX_ATTEMPTS):
            r = client.post(
                "/login",
                data={"username": bad_user, "password": "wrong"},
                follow_redirects=False,
            )
            assert r.status_code == 303
            assert "/login?error=invalid" in r.headers.get("location", "")

        # Bir sonraki deneme rate_limited
        r_blocked = client.post(
            "/login",
            data={"username": bad_user, "password": "wrong"},
            follow_redirects=False,
        )
        assert r_blocked.status_code == 303
        assert "/login?error=rate_limited" in r_blocked.headers.get("location", "")
    finally:
        asyncio.run(_purge_test_ip_failed_logins(db))


def test_rate_limit_audit_log_entry_format() -> None:
    """Failed login audit_log'a 'ip=<addr> user=<name> reason=<r>' yazar."""
    db = _get_db()
    if db is None:
        pytest.skip("DB yok")

    asyncio.run(_purge_test_ip_failed_logins(db))

    client = _client()
    bad_user = f"TEST_auditfmt_{int(datetime.now(UTC).timestamp())}"

    try:
        client.post(
            "/login",
            data={"username": bad_user, "password": "wrong"},
            follow_redirects=False,
        )

        async def _check() -> str:
            pool: Any = cast(Any, db)._get_pool()
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT detail FROM audit_log WHERE category='auth' "
                    "AND action='login_failed' AND detail LIKE 'ip=testclient %'"
                    " ORDER BY timestamp DESC LIMIT 1"
                )
            return cast(str, row["detail"]) if row else ""

        detail = asyncio.run(_check())
        assert detail.startswith("ip=testclient")
        assert f"user={bad_user}" in detail
        assert "reason=invalid_user" in detail
    finally:
        asyncio.run(_purge_test_ip_failed_logins(db))


# --- H-2 (29 Nis 2026 denetim): username-bazlı rate limit ---


async def _purge_username_failed_logins(
    db: DatabaseInterface,
    username: str,
) -> None:
    """Belirli username icin tum login_failed audit kayitlarini siler.

    LIKE wildcard'i icin escape gerekmez: testlerde TEST_ prefix'li alfanumerik
    username uretiyoruz, ozel karakter yok. Cleanup'in kendisi hassas degil.
    """
    pool: Any = cast(Any, db)._get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM audit_log WHERE category='auth' "
            "AND action='login_failed' AND detail LIKE '% user=' || $1 || ' reason=%'",
            username,
        )


def test_username_rate_limit_blocks_distributed_brute_force(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """H-2: Aynı username farklı IP'lerden gelse bile eşik aşılınca bloklanır.

    Senaryo: 3 farklı IP'den 3 başarısız login audit_log'a manuel insert
    edilir (saldırgan IP rotasyonu simülasyonu). Eşik 3'e indirildiği için
    bir sonraki testclient login'i username sayımına takılır.
    """
    db = _get_db()
    if db is None:
        pytest.skip("DB yok")

    # Eşiği test-scope'lu düşür — 10 manuel insert pratik değil.
    monkeypatch.setattr(
        "custos.analytics.dashboard.auth_routes."
        "LOGIN_RATE_LIMIT_USERNAME_MAX_ATTEMPTS",
        3,
    )

    bad_user = f"TEST_userlimit_{int(datetime.now(UTC).timestamp())}"

    async def _seed_distributed_failures() -> None:
        for i in range(3):
            await db.insert_audit_log(
                AuditLogEntry(
                    category="auth",
                    action="login_failed",
                    entity_type="user",
                    entity_id="",
                    detail=(
                        f"ip=192.168.1.{i + 10} user={bad_user} "
                        f"reason=invalid_user"
                    ),
                ),
            )

    asyncio.run(_seed_distributed_failures())

    try:
        client = _client()
        # Eşik 3, manuel 3 başarısız var → bir sonraki istek username
        # sayımına takılır (testclient IP'sinden 0 başarısız ama username
        # toplamı 3).
        r = client.post(
            "/login",
            data={"username": bad_user, "password": "wrong"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert "/login?error=rate_limited" in r.headers.get("location", "")
    finally:
        asyncio.run(_purge_username_failed_logins(db, bad_user))
        asyncio.run(_purge_test_ip_failed_logins(db))


def test_username_failed_login_count_escapes_wildcards() -> None:
    """H-2: LIKE wildcard'lari (%, _, \\) username escape ile kaçirilir.

    user_a icin 3 audit kaydi varsa, user_b='user_a%' (% wildcard) ile
    sayim 0 dönmeli — escape calismiyorsa user_a'yi da yakalar (3 döner).
    """
    db = _get_db()
    if db is None:
        pytest.skip("DB yok")

    ts = int(datetime.now(UTC).timestamp())
    user_a = f"TEST_wildcard_{ts}"
    user_b_pattern = f"{user_a[:-1]}%"  # son karakteri % ile değiştir

    async def _seed() -> None:
        for _ in range(3):
            await db.insert_audit_log(
                AuditLogEntry(
                    category="auth",
                    action="login_failed",
                    entity_type="user",
                    entity_id="",
                    detail=f"ip=10.0.0.1 user={user_a} reason=invalid_user",
                ),
            )

    async def _count(username: str) -> int:
        since = datetime.now(UTC) - timedelta(hours=1)
        return await db.count_recent_failed_logins_by_username(username, since)

    asyncio.run(_seed())

    try:
        # Tam eşleşme: 3 dönmeli
        assert asyncio.run(_count(user_a)) == 3
        # Wildcard pattern: escape ile sayim 0 dönmeli (literal % aranır)
        assert asyncio.run(_count(user_b_pattern)) == 0
    finally:
        asyncio.run(_purge_username_failed_logins(db, user_a))
