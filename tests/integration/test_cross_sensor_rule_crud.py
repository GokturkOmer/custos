"""R-06 / V11-305: Cross-sensor rule CRUD integration testleri.

TimescaleDB ayakta olmasını gerektirir. Tag fixture'ları ``TEST_R06CR_*``
prefix'i ile oluşturulur.
"""

from __future__ import annotations

import asyncio

import pytest

from custos.shared.config import Settings
from custos.shared.database import (
    CrossSensorRule,
    TagRecord,
    TimescaleDBDatabase,
)


@pytest.fixture
def _check_db_available() -> None:
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
    s = Settings()
    database = TimescaleDBDatabase(s)
    await database.connect()
    pool = database._get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM cross_sensor_rules WHERE name LIKE 'TEST_R06CR_%'",
        )
        await conn.execute("DELETE FROM tags WHERE tag_id LIKE 'TEST_R06CR_%'")
    yield database  # type: ignore[misc]
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM cross_sensor_rules WHERE name LIKE 'TEST_R06CR_%'",
        )
        await conn.execute("DELETE FROM tags WHERE tag_id LIKE 'TEST_R06CR_%'")
    await database.close()


async def _make_two_tags(db: TimescaleDBDatabase) -> tuple[TagRecord, TagRecord]:
    a = await db.insert_tag(
        TagRecord(
            tag_id="TEST_R06CR_A",
            name="Tag A",
            modbus_host="127.0.0.1",
            register_address=40001,
        ),
    )
    b = await db.insert_tag(
        TagRecord(
            tag_id="TEST_R06CR_B",
            name="Tag B",
            modbus_host="127.0.0.1",
            register_address=40002,
        ),
    )
    return a, b


@pytest.mark.usefixtures("_check_db_available")
async def test_insert_and_get_cross_sensor_rule(
    db: TimescaleDBDatabase,
) -> None:
    a, b = await _make_two_tags(db)
    assert a.id is not None and b.id is not None

    rule = await db.insert_cross_sensor_rule(
        CrossSensorRule(
            name="TEST_R06CR_simple",
            tag_a_id=a.id,
            tag_b_id=b.id,
            operator="lt",
            severity="warn",
            description="basit",
        ),
    )
    assert rule.id is not None
    assert rule.operator == "lt"
    assert rule.severity == "warn"
    assert rule.enabled is True

    fetched = await db.get_cross_sensor_rule(rule.id)
    assert fetched is not None
    assert fetched.name == "TEST_R06CR_simple"
    assert fetched.description == "basit"


@pytest.mark.usefixtures("_check_db_available")
async def test_update_cross_sensor_rule_whitelist(
    db: TimescaleDBDatabase,
) -> None:
    a, b = await _make_two_tags(db)
    assert a.id is not None and b.id is not None
    rule = await db.insert_cross_sensor_rule(
        CrossSensorRule(
            name="TEST_R06CR_upd",
            tag_a_id=a.id,
            tag_b_id=b.id,
            operator="lt",
            severity="warn",
        ),
    )
    assert rule.id is not None

    updated = await db.update_cross_sensor_rule(
        rule.id,
        {"name": "TEST_R06CR_upd_new", "enabled": False, "severity": "crit"},
    )
    assert updated is not None
    assert updated.name == "TEST_R06CR_upd_new"
    assert updated.enabled is False
    assert updated.severity == "crit"

    # Whitelist dışı alan ValueError.
    with pytest.raises(ValueError):
        await db.update_cross_sensor_rule(rule.id, {"tag_a_id": 9999})


@pytest.mark.usefixtures("_check_db_available")
async def test_delete_cross_sensor_rule(db: TimescaleDBDatabase) -> None:
    a, b = await _make_two_tags(db)
    assert a.id is not None and b.id is not None
    rule = await db.insert_cross_sensor_rule(
        CrossSensorRule(
            name="TEST_R06CR_del",
            tag_a_id=a.id,
            tag_b_id=b.id,
            operator="gt",
            severity="info",
        ),
    )
    assert rule.id is not None

    deleted = await db.delete_cross_sensor_rule(rule.id)
    assert deleted is True
    assert await db.get_cross_sensor_rule(rule.id) is None


@pytest.mark.usefixtures("_check_db_available")
async def test_list_cross_sensor_rules_enabled_filter(
    db: TimescaleDBDatabase,
) -> None:
    a, b = await _make_two_tags(db)
    assert a.id is not None and b.id is not None
    await db.insert_cross_sensor_rule(
        CrossSensorRule(
            name="TEST_R06CR_on",
            tag_a_id=a.id,
            tag_b_id=b.id,
            operator="lt",
            severity="warn",
            enabled=True,
        ),
    )
    await db.insert_cross_sensor_rule(
        CrossSensorRule(
            name="TEST_R06CR_off",
            tag_a_id=a.id,
            tag_b_id=b.id,
            operator="gt",
            severity="warn",
            enabled=False,
        ),
    )

    enabled_only = await db.list_cross_sensor_rules(enabled=True)
    enabled_names = {r.name for r in enabled_only}
    assert "TEST_R06CR_on" in enabled_names
    assert "TEST_R06CR_off" not in enabled_names


@pytest.mark.usefixtures("_check_db_available")
async def test_cross_sensor_rule_same_tag_rejected(
    db: TimescaleDBDatabase,
) -> None:
    """DB CHECK constraint: tag_a == tag_b yasak."""
    import asyncpg

    a, _ = await _make_two_tags(db)
    assert a.id is not None
    with pytest.raises(asyncpg.exceptions.CheckViolationError):
        await db.insert_cross_sensor_rule(
            CrossSensorRule(
                name="TEST_R06CR_same",
                tag_a_id=a.id,
                tag_b_id=a.id,
                operator="lt",
                severity="warn",
            ),
        )
