"""Alarm Event CRUD entegrasyon testleri.

TimescaleDB'nin ayakta olmasını gerektirir (docker compose up -d).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
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
async def db() -> AsyncGenerator[TimescaleDBDatabase, None]:
    """Test için DB bağlantısı oluşturur ve temizler."""
    s = Settings()
    database = TimescaleDBDatabase(s)
    await database.connect()
    pool = database._get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM alarm_events WHERE tag_id LIKE 'TEST_%'")
        await conn.execute("DELETE FROM thresholds WHERE tag_id LIKE 'TEST_%'")
        await conn.execute("DELETE FROM tags WHERE tag_id LIKE 'TEST_%'")
    yield database
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


async def _setup_label_user(db: TimescaleDBDatabase) -> int:
    """alarm_event_labels.labeled_by_user_id FK'i için test user."""
    username = "test_label_user"
    existing = await db.get_user_by_username(username)
    if existing is not None:
        return existing.id
    user = await db.create_user(
        username=username,
        password_hash="x" * 60,  # FK ihtiyacı için dummy hash
        role="operator",
    )
    return user.id


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


@pytest.mark.usefixtures("_check_db_available")
async def test_alarm_event_select_left_joins_label(
    db: TimescaleDBDatabase,
) -> None:
    """R-05a: list_alarm_events / get_alarm_event tek sorguda label döner.

    Etiketli alarm → ``AlarmEvent.label`` dolu (AlarmEventLabel).
    Etiketsiz alarm → ``AlarmEvent.label`` None.
    """
    t = await _setup_threshold(db)
    assert t.id is not None
    user_id = await _setup_label_user(db)

    now = datetime.now(UTC)
    labeled_alarm = await db.insert_alarm_event(
        AlarmEvent(
            threshold_id=t.id,
            tag_id="TEST_ALM",
            state="triggered",
            triggered_at=now,
            trigger_value=85.0,
        ),
    )
    assert labeled_alarm.id is not None
    # Yeni alarm etiketsiz başlar (insert RETURNING'in label=None default'u)
    assert labeled_alarm.label is None

    unlabeled_alarm = await db.insert_alarm_event(
        AlarmEvent(
            threshold_id=t.id,
            tag_id="TEST_ALM",
            state="triggered",
            triggered_at=now,
            trigger_value=90.0,
        ),
    )
    assert unlabeled_alarm.id is not None

    # Sadece ilkini etiketle.
    label = await db.upsert_alarm_label(
        alarm_event_id=labeled_alarm.id,
        label_class="gercek_ariza",
        labeled_by_user_id=user_id,
        notes="Entegrasyon testi",
    )
    assert label.id is not None

    # get_alarm_event — etiketli
    fetched_labeled = await db.get_alarm_event(labeled_alarm.id)
    assert fetched_labeled is not None
    assert fetched_labeled.label is not None
    assert fetched_labeled.label.label_class == "gercek_ariza"
    assert fetched_labeled.label.labeled_by_user_id == user_id
    assert fetched_labeled.label.notes == "Entegrasyon testi"
    assert fetched_labeled.label.alarm_event_id == labeled_alarm.id

    # get_alarm_event — etiketsiz
    fetched_unlabeled = await db.get_alarm_event(unlabeled_alarm.id)
    assert fetched_unlabeled is not None
    assert fetched_unlabeled.label is None

    # list_alarm_events — iki alarm da bu tag için tek sorguda label ile döner
    rows = await db.list_alarm_events(tag_id="TEST_ALM", state="triggered")
    by_id = {a.id: a for a in rows if a.id is not None}
    assert labeled_alarm.id in by_id
    assert unlabeled_alarm.id in by_id
    listed_labeled = by_id[labeled_alarm.id]
    listed_unlabeled = by_id[unlabeled_alarm.id]
    assert listed_labeled.label is not None
    assert listed_labeled.label.label_class == "gercek_ariza"
    assert listed_unlabeled.label is None
