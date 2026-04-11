"""Alarm Event CRUD entegrasyon testleri.

TimescaleDB'nin ayakta olmasını gerektirir (docker compose up -d).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from custos.shared.config import Settings
from custos.shared.database import (
    AlarmEvent,
    TagRecord,
    Threshold,
    TimescaleDBDatabase,
)


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
async def db() -> TimescaleDBDatabase:
    """Test için DB bağlantısı oluşturur ve temizler."""
    s = Settings()
    database = TimescaleDBDatabase(s)
    await database.connect()
    pool = database._get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM alarm_events WHERE tag_id LIKE 'TEST_%'")
        await conn.execute("DELETE FROM thresholds WHERE tag_id LIKE 'TEST_%'")
        await conn.execute("DELETE FROM tags WHERE tag_id LIKE 'TEST_%'")
    yield database  # type: ignore[misc]
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM alarm_events WHERE tag_id LIKE 'TEST_%'")
        await conn.execute("DELETE FROM thresholds WHERE tag_id LIKE 'TEST_%'")
        await conn.execute("DELETE FROM tags WHERE tag_id LIKE 'TEST_%'")
    await database.close()


async def _setup_threshold(db: TimescaleDBDatabase) -> Threshold:
    """Test için tag + threshold oluşturur."""
    existing = await db.get_tag("TEST_ALM")
    if existing is None:
        await db.insert_tag(
            TagRecord(
                tag_id="TEST_ALM",
                name="Test Alarm Tag",
                modbus_host="127.0.0.1",
                register_address=40001,
            ),
        )
    return await db.insert_threshold(
        Threshold(tag_id="TEST_ALM", name="Test Alarm Threshold", set_point=80.0),
    )


@pytest.mark.usefixtures("_check_db_available")
async def test_insert_alarm_event(db: TimescaleDBDatabase) -> None:
    """Alarm event oluşturup geri okunabiliyor mu?"""
    t = await _setup_threshold(db)
    assert t.id is not None

    now = datetime.now(UTC)
    event = AlarmEvent(
        threshold_id=t.id,
        tag_id="TEST_ALM",
        state="triggered",
        triggered_at=now,
        trigger_value=85.5,
    )
    created = await db.insert_alarm_event(event)

    assert created.id is not None
    assert created.state == "triggered"
    assert created.trigger_value == 85.5

    fetched = await db.get_alarm_event(created.id)
    assert fetched is not None
    assert fetched.tag_id == "TEST_ALM"


@pytest.mark.usefixtures("_check_db_available")
async def test_update_alarm_event_acknowledge(db: TimescaleDBDatabase) -> None:
    """Alarm acknowledge çalışıyor mu?"""
    t = await _setup_threshold(db)
    assert t.id is not None

    now = datetime.now(UTC)
    created = await db.insert_alarm_event(
        AlarmEvent(
            threshold_id=t.id,
            tag_id="TEST_ALM",
            triggered_at=now,
            trigger_value=90.0,
        ),
    )
    assert created.id is not None

    updated = await db.update_alarm_event(
        created.id,
        {"state": "acknowledged", "acknowledged_at": now},
    )
    assert updated is not None
    assert updated.state == "acknowledged"
    assert updated.acknowledged_at is not None


@pytest.mark.usefixtures("_check_db_available")
async def test_update_alarm_event_clear(db: TimescaleDBDatabase) -> None:
    """Alarm temizleme çalışıyor mu?"""
    t = await _setup_threshold(db)
    assert t.id is not None

    now = datetime.now(UTC)
    created = await db.insert_alarm_event(
        AlarmEvent(
            threshold_id=t.id,
            tag_id="TEST_ALM",
            triggered_at=now,
            trigger_value=90.0,
        ),
    )
    assert created.id is not None

    updated = await db.update_alarm_event(
        created.id,
        {"state": "cleared", "cleared_at": now, "clear_value": 70.0},
    )
    assert updated is not None
    assert updated.state == "cleared"
    assert updated.clear_value == 70.0


@pytest.mark.usefixtures("_check_db_available")
async def test_get_active_alarm_for_threshold(db: TimescaleDBDatabase) -> None:
    """Aktif alarm doğru getirilyor mu?"""
    t = await _setup_threshold(db)
    assert t.id is not None

    now = datetime.now(UTC)
    # Aktif alarm yok
    assert await db.get_active_alarm_for_threshold(t.id) is None

    # Triggered alarm oluştur
    created = await db.insert_alarm_event(
        AlarmEvent(
            threshold_id=t.id,
            tag_id="TEST_ALM",
            triggered_at=now,
            trigger_value=85.0,
        ),
    )
    assert created.id is not None

    active = await db.get_active_alarm_for_threshold(t.id)
    assert active is not None
    assert active.state == "triggered"

    # Alarm temizle
    await db.update_alarm_event(
        created.id,
        {"state": "cleared", "cleared_at": now, "clear_value": 70.0},
    )
    assert await db.get_active_alarm_for_threshold(t.id) is None


@pytest.mark.usefixtures("_check_db_available")
async def test_list_alarm_events_filter_by_state(db: TimescaleDBDatabase) -> None:
    """State filtresi çalışıyor mu?"""
    t = await _setup_threshold(db)
    assert t.id is not None

    now = datetime.now(UTC)
    e1 = await db.insert_alarm_event(
        AlarmEvent(
            threshold_id=t.id,
            tag_id="TEST_ALM",
            state="triggered",
            triggered_at=now,
            trigger_value=85.0,
        ),
    )
    assert e1.id is not None
    await db.update_alarm_event(e1.id, {"state": "cleared", "cleared_at": now})

    await db.insert_alarm_event(
        AlarmEvent(
            threshold_id=t.id,
            tag_id="TEST_ALM",
            state="triggered",
            triggered_at=now,
            trigger_value=90.0,
        ),
    )

    triggered = await db.list_alarm_events(state="triggered")
    test_triggered = [e for e in triggered if e.tag_id == "TEST_ALM"]
    assert len(test_triggered) >= 1

    cleared = await db.list_alarm_events(state="cleared")
    test_cleared = [e for e in cleared if e.tag_id == "TEST_ALM"]
    assert len(test_cleared) >= 1
