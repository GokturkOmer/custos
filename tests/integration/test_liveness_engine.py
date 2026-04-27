"""Liveness engine entegrasyon testleri (V11-108, P-05).

TimescaleDB'nin ayakta olmasını gerektirir (docker compose up -d).

Senaryolar (4 integration):

1. ``test_liveness_alarm_writes_is_test_flag``: Bakım modu kapalıyken
   stuck-at tetiklendiğinde ``is_test=False`` ile yazılır.
2. ``test_liveness_skip_during_maintenance``: Global bakım modunda
   stuck-at alarm'ı ``is_test=True`` ile yazılır (push gitmez).
3. ``test_liveness_cooldown_prevents_repeat``: Aynı tag için 1 saat
   içinde tekrar alarm üretilmez.
4. ``test_alarm_page_filter_by_source_liveness``: ``list_alarm_events``
   ``source='liveness'`` filtresi yalnızca liveness alarm'larını döner.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from custos.analytics import maintenance_mode
from custos.analytics.liveness_engine import LivenessEngine
from custos.shared.config import Settings
from custos.shared.database import (
    AlarmEvent,
    TagReading,
    TagRecord,
    Threshold,
    TimescaleDBDatabase,
)

_TEST_TAG_PREFIX = "TEST_LV_"


async def _cleanup(pool: asyncpg.Pool) -> None:
    """Liveness test için TEST_LV_ prefix'li satırları + global bakımı temizler."""
    async with pool.acquire() as conn:
        await conn.execute(
            f"DELETE FROM alarm_events WHERE tag_id LIKE '{_TEST_TAG_PREFIX}%'",
        )
        await conn.execute(
            f"DELETE FROM thresholds WHERE tag_id LIKE '{_TEST_TAG_PREFIX}%'",
        )
        await conn.execute(
            f"DELETE FROM tag_readings WHERE tag_id LIKE '{_TEST_TAG_PREFIX}%'",
        )
        await conn.execute(
            f"DELETE FROM tags WHERE tag_id LIKE '{_TEST_TAG_PREFIX}%'",
        )
        await conn.execute(
            "UPDATE retention_config SET "
            "    global_maintenance_until = NULL, "
            "    global_maintenance_reason = '', "
            "    global_maintenance_started_by_user_id = NULL, "
            "    global_maintenance_started_at = NULL "
            "WHERE id = 1",
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
    """Liveness test fixture — connect + cleanup before/after."""
    s = Settings()
    database = TimescaleDBDatabase(s)
    await database.connect()
    pool = database._get_pool()
    await _cleanup(pool)
    yield database  # type: ignore[misc]
    await _cleanup(pool)
    await database.close()


async def _seed_stuck_tag(
    db: TimescaleDBDatabase,
    tag_id: str,
    *,
    preset: str = "fast",
    seconds_override: int | None = None,
    stuck_seconds: int = 600,
) -> TagRecord:
    """Stuck-at senaryosu için tag + 4 noktalı sabit-değer reading'i yazar.

    ``stuck_seconds``: ``now - stuck_seconds`` ile ``now`` arasında 4 nokta
    aynı değerle yazılır → stuck eşiği aşılırsa alarm tetiklenir.
    """
    tag = TagRecord(
        tag_id=tag_id,
        name=f"Test Liveness {tag_id}",
        modbus_host="127.0.0.1",
        register_address=40001,
        unit="bar",  # 'auto' fallback'i için 'fast' (300s) verir; preset
        # parametresiyle override ederiz.
        stuck_at_preset=preset,
        stuck_at_seconds=seconds_override,
    )
    await db.insert_tag(tag)

    now = datetime.now(UTC)
    readings = [
        TagReading(timestamp=now - timedelta(seconds=stuck_seconds), tag_id=tag_id, value=42.0),
        TagReading(
            timestamp=now - timedelta(seconds=int(stuck_seconds * 2 / 3)),
            tag_id=tag_id,
            value=42.0,
        ),
        TagReading(
            timestamp=now - timedelta(seconds=int(stuck_seconds / 3)),
            tag_id=tag_id,
            value=42.0,
        ),
        TagReading(timestamp=now, tag_id=tag_id, value=42.0),
    ]
    await db.insert_tag_readings_batch(readings)
    return tag


@pytest.mark.usefixtures("_check_db_available")
async def test_liveness_alarm_writes_is_test_flag(
    db: TimescaleDBDatabase,
) -> None:
    """Bakım modu kapalıyken liveness alarm ``is_test=False`` yazılır."""
    tag_id = f"{_TEST_TAG_PREFIX}NORMAL"
    # 'fast' preset 300s; 600s sabit değer → tetiklenir
    await _seed_stuck_tag(db, tag_id, preset="fast", stuck_seconds=600)

    engine = LivenessEngine(db=db)
    await engine._tick()

    alarms = await db.list_alarm_events(tag_id=tag_id, source="liveness")
    assert len(alarms) == 1
    alarm = alarms[0]
    assert alarm.is_test is False
    assert alarm.threshold_id is None
    assert alarm.severity == "warn"
    assert "donuk" in alarm.message.lower() or "Sensör" in alarm.message


@pytest.mark.usefixtures("_check_db_available")
async def test_liveness_skip_during_maintenance(
    db: TimescaleDBDatabase,
) -> None:
    """Global bakım modunda liveness alarm ``is_test=True`` ile yazılır."""
    tag_id = f"{_TEST_TAG_PREFIX}MAINT"
    await _seed_stuck_tag(db, tag_id, preset="fast", stuck_seconds=600)

    # Global bakım modunu aç (sınırsız manuel)
    await maintenance_mode.start_global_maintenance(
        db,
        until=None,
        reason="Test bakım — liveness atlama",
        user_id=1,
    )

    engine = LivenessEngine(db=db)
    await engine._tick()

    alarms = await db.list_alarm_events(tag_id=tag_id, source="liveness")
    assert len(alarms) == 1
    assert alarms[0].is_test is True


@pytest.mark.usefixtures("_check_db_available")
async def test_liveness_cooldown_prevents_repeat(
    db: TimescaleDBDatabase,
) -> None:
    """1 saat cooldown — aynı tag için iki tick'te tek alarm üretilir."""
    tag_id = f"{_TEST_TAG_PREFIX}COOLDOWN"
    await _seed_stuck_tag(db, tag_id, preset="fast", stuck_seconds=600)

    engine = LivenessEngine(db=db)
    await engine._tick()
    await engine._tick()  # Hemen ikinci tick — cooldown devrede olmalı

    alarms = await db.list_alarm_events(tag_id=tag_id, source="liveness")
    assert len(alarms) == 1


@pytest.mark.usefixtures("_check_db_available")
async def test_alarm_page_filter_by_source_liveness(
    db: TimescaleDBDatabase,
) -> None:
    """``list_alarm_events(source='liveness')`` yalnızca liveness'i döner."""
    # Bir threshold + threshold alarm'ı yaz (mevcut tablo akışı)
    tag_threshold = TagRecord(
        tag_id=f"{_TEST_TAG_PREFIX}THR",
        name="Threshold Tag",
        modbus_host="127.0.0.1",
        register_address=40010,
    )
    await db.insert_tag(tag_threshold)
    threshold = await db.insert_threshold(
        Threshold(
            tag_id=tag_threshold.tag_id,
            name="Test Threshold",
            set_point=80.0,
            debounce_seconds=0,
        ),
    )
    assert threshold.id is not None

    now = datetime.now(UTC)
    await db.insert_alarm_event(
        AlarmEvent(
            tag_id=tag_threshold.tag_id,
            threshold_id=threshold.id,
            triggered_at=now,
            trigger_value=85.0,
            source="threshold",
            severity="crit",
        ),
    )

    # Bir liveness alarm'ı (yine senaryo üzerinden engine ile)
    await _seed_stuck_tag(
        db,
        f"{_TEST_TAG_PREFIX}LV",
        preset="fast",
        stuck_seconds=600,
    )
    engine = LivenessEngine(db=db)
    await engine._tick()

    # Filtre yalnızca liveness'i dönmeli
    only_liveness = await db.list_alarm_events(source="liveness")
    only_threshold = await db.list_alarm_events(source="threshold")

    test_liveness = [
        a for a in only_liveness if a.tag_id.startswith(_TEST_TAG_PREFIX)
    ]
    test_threshold = [
        a for a in only_threshold if a.tag_id.startswith(_TEST_TAG_PREFIX)
    ]
    assert len(test_liveness) == 1
    assert test_liveness[0].source == "liveness"
    assert len(test_threshold) == 1
    assert test_threshold[0].source == "threshold"
