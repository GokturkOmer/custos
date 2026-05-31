"""ThresholdWatcher entegrasyon testleri (Critical loop alarm üretimi, review H1).

Eşik tabanlı alarm üretimi ThresholdEngine'den Critical loop'taki
``ThresholdWatcher``'a taşındı. Watcher okumaları DB'den değil, Collector'ın
yayımladığı in-memory snapshot'tan (``reading_source``) alır; testte bu kaynak
bir dict ile taklit edilir. Tag + threshold tanımı + alarm yazımı gerçek DB'ye
gider (threshold→tag FK olduğundan tag da oluşturulur).

TimescaleDB'nin ayakta olmasını gerektirir (docker compose up -d).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from custos.critical.threshold_watcher import ThresholdWatcher
from custos.shared.config import Settings
from custos.shared.database import (
    TagReading,
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


async def _clean(db: TimescaleDBDatabase) -> None:
    pool = db._get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM audit_log WHERE entity_id LIKE 'TEST_%'")
        await conn.execute("DELETE FROM alarm_events WHERE tag_id LIKE 'TEST_%'")
        await conn.execute("DELETE FROM thresholds WHERE tag_id LIKE 'TEST_%'")
        await conn.execute("DELETE FROM tags WHERE tag_id LIKE 'TEST_%'")


@pytest.fixture
async def db() -> TimescaleDBDatabase:
    """Test için DB bağlantısı oluşturur ve temizler."""
    s = Settings()
    database = TimescaleDBDatabase(s)
    await database.connect()
    await _clean(database)
    yield database  # type: ignore[misc]
    await _clean(database)
    await database.close()


def _reading(tag_id: str, value: float) -> TagReading:
    """Tek bir in-memory TagReading üretir (watcher reading_source için)."""
    return TagReading(timestamp=datetime.now(UTC), tag_id=tag_id, value=value)


async def _setup_threshold(db: TimescaleDBDatabase, *, tag_id: str, **kw: object) -> Threshold:
    """Tag (threshold→tag FK için) + threshold oluşturur."""
    if await db.get_tag(tag_id) is None:
        await db.insert_tag(
            TagRecord(
                tag_id=tag_id,
                name=f"Test {tag_id}",
                modbus_host="127.0.0.1",
                register_address=40001,
            ),
        )
    return await db.insert_threshold(Threshold(tag_id=tag_id, name=f"Threshold {tag_id}", **kw))  # type: ignore[arg-type]


async def _watcher(
    db: TimescaleDBDatabase,
    source: Callable[[], dict[str, TagReading]],
) -> ThresholdWatcher:
    """Watcher kurar + threshold tanımlarını yükler (tek refresh)."""
    w = ThresholdWatcher(db=db, reading_source=source)
    await w._refresh_definitions()
    return w


@pytest.mark.usefixtures("_check_db_available")
async def test_watcher_triggers_alarm_on_high_breach(db: TimescaleDBDatabase) -> None:
    """Eşik aşımında (debounce sonrası) alarm tetikleniyor mu?"""
    threshold = await _setup_threshold(
        db, tag_id="TEST_W1", direction="high", set_point=80.0, debounce_seconds=0,
    )
    assert threshold.id is not None
    readings = {"TEST_W1": _reading("TEST_W1", 90.0)}
    w = await _watcher(db, lambda: readings)
    await w._evaluate_cycle()
    await w._evaluate_cycle()

    active = await db.get_active_alarm_for_threshold(threshold.id)
    assert active is not None
    assert active.state == "triggered"
    assert active.trigger_value == 90.0
    assert active.source == "threshold"
    assert active.is_test is False
    assert active.message  # kendi-kendine yeten açıklama dolduruldu


@pytest.mark.usefixtures("_check_db_available")
async def test_watcher_respects_debounce(db: TimescaleDBDatabase) -> None:
    """Debounce süresi dolmadan alarm tetiklenmemeli."""
    threshold = await _setup_threshold(
        db, tag_id="TEST_W2", direction="high", set_point=80.0, debounce_seconds=60,
    )
    assert threshold.id is not None
    readings = {"TEST_W2": _reading("TEST_W2", 90.0)}
    w = await _watcher(db, lambda: readings)
    await w._evaluate_cycle()
    await w._evaluate_cycle()

    assert await db.get_active_alarm_for_threshold(threshold.id) is None


@pytest.mark.usefixtures("_check_db_available")
async def test_watcher_clears_alarm_with_hysteresis(db: TimescaleDBDatabase) -> None:
    """Hysteresis bandı: set_point - hysteresis altına düşünce temizlenir."""
    threshold = await _setup_threshold(
        db, tag_id="TEST_W3", direction="high", set_point=80.0, debounce_seconds=0, hysteresis=5.0,
    )
    assert threshold.id is not None
    readings = {"TEST_W3": _reading("TEST_W3", 90.0)}
    w = await _watcher(db, lambda: readings)
    await w._evaluate_cycle()
    await w._evaluate_cycle()
    assert await db.get_active_alarm_for_threshold(threshold.id) is not None

    readings["TEST_W3"] = _reading("TEST_W3", 77.0)  # 75 üstü → temizlenmez
    await w._evaluate_cycle()
    assert await db.get_active_alarm_for_threshold(threshold.id) is not None

    readings["TEST_W3"] = _reading("TEST_W3", 74.0)  # 75 altı → temizlenir
    await w._evaluate_cycle()
    assert await db.get_active_alarm_for_threshold(threshold.id) is None


@pytest.mark.usefixtures("_check_db_available")
async def test_watcher_ignores_disabled_threshold(db: TimescaleDBDatabase) -> None:
    """Pasif threshold değerlendirilmemeli."""
    threshold = await _setup_threshold(
        db, tag_id="TEST_W4", direction="high", set_point=80.0, debounce_seconds=0, enabled=False,
    )
    assert threshold.id is not None
    readings = {"TEST_W4": _reading("TEST_W4", 90.0)}
    w = await _watcher(db, lambda: readings)
    await w._evaluate_cycle()
    await w._evaluate_cycle()

    assert await db.get_active_alarm_for_threshold(threshold.id) is None


@pytest.mark.usefixtures("_check_db_available")
async def test_watcher_low_direction_breach_and_clear(db: TimescaleDBDatabase) -> None:
    """Low direction (alt eşik) breach + hysteresis clear yolu."""
    threshold = await _setup_threshold(
        db, tag_id="TEST_W5", direction="low", set_point=10.0, debounce_seconds=0, hysteresis=2.0,
    )
    assert threshold.id is not None
    readings = {"TEST_W5": _reading("TEST_W5", 5.0)}
    w = await _watcher(db, lambda: readings)
    await w._evaluate_cycle()
    await w._evaluate_cycle()
    active = await db.get_active_alarm_for_threshold(threshold.id)
    assert active is not None
    assert active.trigger_value == 5.0

    readings["TEST_W5"] = _reading("TEST_W5", 11.0)  # 12 altı → temizlenmez
    await w._evaluate_cycle()
    assert await db.get_active_alarm_for_threshold(threshold.id) is not None

    readings["TEST_W5"] = _reading("TEST_W5", 13.0)  # 12 üstü → temizlenir
    await w._evaluate_cycle()
    assert await db.get_active_alarm_for_threshold(threshold.id) is None


@pytest.mark.usefixtures("_check_db_available")
async def test_watcher_emergency_skips_auto_clear(db: TimescaleDBDatabase) -> None:
    """Emergency severity: hysteresis ile auto-clear OLMAZ (manuel ack zorunlu)."""
    threshold = await _setup_threshold(
        db, tag_id="TEST_W6", direction="high", set_point=80.0,
        severity="emergency", debounce_seconds=0, hysteresis=5.0,
    )
    assert threshold.id is not None
    readings = {"TEST_W6": _reading("TEST_W6", 90.0)}
    w = await _watcher(db, lambda: readings)
    await w._evaluate_cycle()
    await w._evaluate_cycle()
    assert await db.get_active_alarm_for_threshold(threshold.id) is not None

    readings["TEST_W6"] = _reading("TEST_W6", 10.0)  # normale döndü ama emergency
    await w._evaluate_cycle()
    assert await db.get_active_alarm_for_threshold(threshold.id) is not None


@pytest.mark.usefixtures("_check_db_available")
async def test_watcher_emergency_audit_category(db: TimescaleDBDatabase) -> None:
    """Emergency tetiklendiğinde audit_log category'si 'alarm_emergency'."""
    threshold = await _setup_threshold(
        db, tag_id="TEST_W7", direction="high", set_point=80.0,
        severity="emergency", debounce_seconds=0,
    )
    assert threshold.id is not None
    readings = {"TEST_W7": _reading("TEST_W7", 95.0)}
    w = await _watcher(db, lambda: readings)
    await w._evaluate_cycle()
    await w._evaluate_cycle()

    pool = db._get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT category, action FROM audit_log WHERE entity_id = $1",
            str(threshold.id),
        )
    assert "alarm_emergency" in {r["category"] for r in rows}
    assert "emergency_alarm_triggered" in {r["action"] for r in rows}


@pytest.mark.usefixtures("_check_db_available")
async def test_watcher_clears_debounce_tracker_when_reading_disappears(
    db: TimescaleDBDatabase,
) -> None:
    """Debounce başladıktan sonra bu tag'in okuması kaybolursa tracker temizlenmeli."""
    threshold = await _setup_threshold(
        db, tag_id="TEST_W8", direction="high", set_point=80.0, debounce_seconds=60,
    )
    assert threshold.id is not None
    readings: dict[str, TagReading] = {"TEST_W8": _reading("TEST_W8", 90.0)}
    w = await _watcher(db, lambda: readings)
    await w._evaluate_cycle()
    assert threshold.id in w._debounce_tracker

    # Bu tag'in okuması kayboldu (Collector başka tag yayımlıyor ama bunu değil)
    readings.clear()
    readings["TEST_W8_OTHER"] = _reading("TEST_W8_OTHER", 1.0)
    await w._evaluate_cycle()
    assert threshold.id not in w._debounce_tracker
    assert await db.get_active_alarm_for_threshold(threshold.id) is None
