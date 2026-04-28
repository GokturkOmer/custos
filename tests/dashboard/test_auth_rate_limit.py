"""PP-06 — IP-bazlı login brute-force koruması testi.

audit_log'taki son LOGIN_RATE_LIMIT_WINDOW_MINUTES içindeki
'login_failed' kayıtları sayılır; eşik LOGIN_RATE_LIMIT_MAX_ATTEMPTS
aşıldığında 'rate_limited' error ile reddedilir.

DB gerektirir; yoksa skip.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from custos.__main__ import app
from custos.analytics.dashboard.auth_routes import (
    LOGIN_RATE_LIMIT_MAX_ATTEMPTS,
)
from custos.shared.database import DatabaseInterface


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
