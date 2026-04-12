"""Push Subscription CRUD entegrasyon testleri.

TimescaleDB'nin ayakta olmasını gerektirir (docker compose up -d).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import time

import pytest

from custos.shared.config import Settings
from custos.shared.database import PushSubscription, TimescaleDBDatabase


@pytest.fixture
def _check_db_available() -> None:
    """TimescaleDB erişilebilir değilse testi atla."""

    async def _probe() -> bool:
        s = Settings()
        db = TimescaleDBDatabase(s)
        try:
            await db.connect()
            result = await db.health_check()
            await db.close()
        except Exception:
            return False
        else:
            return result

    if not asyncio.run(_probe()):
        pytest.skip("TimescaleDB ayakta değil — 'docker compose up -d' çalıştır")


@pytest.fixture
async def db() -> AsyncIterator[TimescaleDBDatabase]:
    """Test için DB bağlantısı oluşturur ve test verilerini temizler."""
    s = Settings()
    database = TimescaleDBDatabase(s)
    await database.connect()

    pool = database._get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM push_subscriptions WHERE endpoint LIKE 'https://test.%'"
        )
    yield database  # type: ignore[misc]
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM push_subscriptions WHERE endpoint LIKE 'https://test.%'"
        )
    await database.close()


@pytest.mark.usefixtures("_check_db_available")
async def test_upsert_push_subscription(db: TimescaleDBDatabase) -> None:
    """Yeni subscription oluşturur ve upsert ile günceller."""
    sub = PushSubscription(
        endpoint="https://test.push/sub1",
        p256dh="test-p256dh-key-1",
        auth="test-auth-key-1",
    )
    created = await db.upsert_push_subscription(sub)
    assert created.id is not None
    assert created.endpoint == "https://test.push/sub1"
    assert created.notify_warn is True
    assert created.notify_crit is True

    # Upsert — aynı endpoint ile key güncelle
    sub2 = PushSubscription(
        endpoint="https://test.push/sub1",
        p256dh="updated-p256dh-key",
        auth="updated-auth-key",
    )
    updated = await db.upsert_push_subscription(sub2)
    assert updated.id == created.id  # Aynı kayıt
    assert updated.p256dh == "updated-p256dh-key"


@pytest.mark.usefixtures("_check_db_available")
async def test_delete_push_subscription(db: TimescaleDBDatabase) -> None:
    """Subscription siler."""
    sub = PushSubscription(
        endpoint="https://test.push/sub-del",
        p256dh="del-p256dh",
        auth="del-auth",
    )
    await db.upsert_push_subscription(sub)

    deleted = await db.delete_push_subscription("https://test.push/sub-del")
    assert deleted is True

    # Olmayan endpoint silme
    deleted2 = await db.delete_push_subscription("https://test.push/nonexistent")
    assert deleted2 is False


@pytest.mark.usefixtures("_check_db_available")
async def test_list_push_subscriptions(db: TimescaleDBDatabase) -> None:
    """Tüm subscription'ları listeler."""
    # İki subscription ekle
    await db.upsert_push_subscription(
        PushSubscription(
            endpoint="https://test.push/list1",
            p256dh="list-p256dh-1",
            auth="list-auth-1",
        ),
    )
    await db.upsert_push_subscription(
        PushSubscription(
            endpoint="https://test.push/list2",
            p256dh="list-p256dh-2",
            auth="list-auth-2",
        ),
    )

    subs = await db.list_push_subscriptions()
    test_subs = [s for s in subs if s.endpoint.startswith("https://test.push/list")]
    assert len(test_subs) >= 2


@pytest.mark.usefixtures("_check_db_available")
async def test_update_subscription_settings(db: TimescaleDBDatabase) -> None:
    """Subscription ayarlarını günceller."""
    sub = PushSubscription(
        endpoint="https://test.push/settings1",
        p256dh="set-p256dh",
        auth="set-auth",
    )
    await db.upsert_push_subscription(sub)

    updated = await db.update_push_subscription_settings(
        endpoint="https://test.push/settings1",
        updates={
            "notify_warn": False,
            "notify_crit": True,
            "quiet_start": time(22, 0),
            "quiet_end": time(7, 0),
        },
    )
    assert updated is not None
    assert updated.notify_warn is False
    assert updated.notify_crit is True
    assert updated.quiet_start == time(22, 0)
    assert updated.quiet_end == time(7, 0)

    # Olmayan endpoint güncelleme
    result = await db.update_push_subscription_settings(
        endpoint="https://test.push/nonexistent",
        updates={"notify_warn": False},
    )
    assert result is None
