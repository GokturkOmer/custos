"""R-06 / V11-304-305: Threshold Engine Layer 1 ek kuralları integration testleri.

TimescaleDB ayakta olmasını gerektirir. Threshold engine tick'i içinde
rate-of-change ve cross-sensor kontrolleri sırasıyla çalıştırılır;
testler bu üç kanalın bağımsız tetiklendiğini ve cooldown davranışını
doğrular.

Tag fixture'ları ``TEST_R06_*`` prefix'i ile oluşturulur — fixture
cleanup bu pattern'i tarayıp siler.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from custos.analytics.threshold_engine import ThresholdEngine
from custos.shared.config import Settings
from custos.shared.database import (
    CrossSensorRule,
    TagReading,
    TagRecord,
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
    """Test için DB bağlantısı oluşturur ve TEST_R06_* prefix'ini temizler."""
    s = Settings()
    database = TimescaleDBDatabase(s)
    await database.connect()
    pool = database._get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM cross_sensor_rules "
            "WHERE name LIKE 'TEST_R06_%'",
        )
        await conn.execute(
            "DELETE FROM audit_log WHERE entity_id LIKE 'TEST_R06_%'",
        )
        await conn.execute(
            "DELETE FROM alarm_events WHERE tag_id LIKE 'TEST_R06_%'",
        )
        await conn.execute(
            "DELETE FROM tag_readings WHERE tag_id LIKE 'TEST_R06_%'",
        )
        await conn.execute("DELETE FROM tags WHERE tag_id LIKE 'TEST_R06_%'")
    yield database  # type: ignore[misc]
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM cross_sensor_rules "
            "WHERE name LIKE 'TEST_R06_%'",
        )
        await conn.execute(
            "DELETE FROM audit_log WHERE entity_id LIKE 'TEST_R06_%'",
        )
        await conn.execute(
            "DELETE FROM alarm_events WHERE tag_id LIKE 'TEST_R06_%'",
        )
        await conn.execute(
            "DELETE FROM tag_readings WHERE tag_id LIKE 'TEST_R06_%'",
        )
        await conn.execute("DELETE FROM tags WHERE tag_id LIKE 'TEST_R06_%'")
    await database.close()


async def _insert_tag(
    db: TimescaleDBDatabase,
    tag_id: str,
    *,
    name: str | None = None,
    rate_of_change_threshold: float | None = None,
) -> TagRecord:
    """Test tag'i oluşturur — yoksa insert eder ve geri döner."""
    existing = await db.get_tag(tag_id)
    if existing is not None:
        return existing
    return await db.insert_tag(
        TagRecord(
            tag_id=tag_id,
            name=name or f"Test {tag_id}",
            modbus_host="127.0.0.1",
            register_address=40001,
            rate_of_change_threshold=rate_of_change_threshold,
        ),
    )


async def _write_reading(
    db: TimescaleDBDatabase,
    tag_id: str,
    value: float,
    when: datetime,
) -> None:
    """Belirli timestamp ile tag okuması yazar."""
    await db.insert_tag_readings_batch(
        [TagReading(timestamp=when, tag_id=tag_id, value=value)],
    )


@pytest.mark.usefixtures("_check_db_available")
async def test_rate_of_change_triggers_alarm_on_fast_jump(
    db: TimescaleDBDatabase,
) -> None:
    """Tag değeri 1 dakikada eşik üstü değiştiğinde alarm tetikler."""
    tag_id = "TEST_R06_RATE_FAST"
    await _insert_tag(db, tag_id, rate_of_change_threshold=2.0)

    base = datetime.now(UTC) - timedelta(seconds=120)
    await _write_reading(db, tag_id, 10.0, base)

    engine = ThresholdEngine(db=db, check_interval_seconds=1.0)
    # İlk tick — referans noktayı hafızaya alır.
    await engine._check_cycle()

    # 60 sn sonra +5.0 değişim → 5.0/dk > 2.0/dk eşik.
    await _write_reading(db, tag_id, 15.0, base + timedelta(seconds=60))
    await engine._check_cycle()

    alarms = await db.list_alarm_events(
        tag_id=tag_id, source="rate_of_change", limit=10,
    )
    assert len(alarms) == 1, "Rate-of-change alarmı tetiklenmedi"
    alarm = alarms[0]
    assert alarm.severity == "warn"
    assert "/dk" in alarm.message
    assert alarm.threshold_id is None  # Layer 1 kuralı, threshold yok


@pytest.mark.usefixtures("_check_db_available")
async def test_rate_of_change_below_threshold_no_alarm(
    db: TimescaleDBDatabase,
) -> None:
    """Eşik altı değişim alarm üretmez."""
    tag_id = "TEST_R06_RATE_SLOW"
    await _insert_tag(db, tag_id, rate_of_change_threshold=10.0)

    base = datetime.now(UTC) - timedelta(seconds=120)
    await _write_reading(db, tag_id, 100.0, base)

    engine = ThresholdEngine(db=db, check_interval_seconds=1.0)
    await engine._check_cycle()

    # 60 sn'de +1.0 → 1.0/dk eşiğin (10.0/dk) altında.
    await _write_reading(db, tag_id, 101.0, base + timedelta(seconds=60))
    await engine._check_cycle()

    alarms = await db.list_alarm_events(
        tag_id=tag_id, source="rate_of_change", limit=10,
    )
    assert len(alarms) == 0


@pytest.mark.usefixtures("_check_db_available")
async def test_rate_of_change_null_threshold_disabled(
    db: TimescaleDBDatabase,
) -> None:
    """rate_of_change_threshold=None tag'lerinde kontrol kapalıdır."""
    tag_id = "TEST_R06_RATE_NULL"
    await _insert_tag(db, tag_id, rate_of_change_threshold=None)

    base = datetime.now(UTC) - timedelta(seconds=120)
    await _write_reading(db, tag_id, 0.0, base)

    engine = ThresholdEngine(db=db, check_interval_seconds=1.0)
    await engine._check_cycle()

    # Çok büyük sıçrama — threshold None olduğu için kontrol yapılmaz.
    await _write_reading(db, tag_id, 1000.0, base + timedelta(seconds=60))
    await engine._check_cycle()

    alarms = await db.list_alarm_events(
        tag_id=tag_id, source="rate_of_change", limit=10,
    )
    assert len(alarms) == 0


@pytest.mark.usefixtures("_check_db_available")
async def test_cross_sensor_rule_lt_violation_triggers_alarm(
    db: TimescaleDBDatabase,
) -> None:
    """Tag A < Tag B beklendiğinde A >= B → alarm üretilir."""
    tag_a = await _insert_tag(db, "TEST_R06_CROSS_A", name="Supply Temp")
    tag_b = await _insert_tag(db, "TEST_R06_CROSS_B", name="Return Temp")
    assert tag_a.id is not None and tag_b.id is not None

    rule = await db.insert_cross_sensor_rule(
        CrossSensorRule(
            name="TEST_R06_supply_lt_return",
            tag_a_id=tag_a.id,
            tag_b_id=tag_b.id,
            operator="lt",
            severity="warn",
            description="Test rule",
        ),
    )
    assert rule.id is not None

    now = datetime.now(UTC)
    # supply (A) = 25.0, return (B) = 20.0 → A < B kuralı ihlal.
    await _write_reading(db, tag_a.tag_id, 25.0, now - timedelta(seconds=2))
    await _write_reading(db, tag_b.tag_id, 20.0, now - timedelta(seconds=2))

    engine = ThresholdEngine(db=db, check_interval_seconds=1.0)
    await engine._check_cycle()

    alarms = await db.list_alarm_events(
        tag_id=tag_a.tag_id, source="cross_sensor", limit=10,
    )
    assert len(alarms) == 1, "Cross-sensor alarmı tetiklenmedi"
    alarm = alarms[0]
    assert alarm.severity == "warn"
    assert "Cross-sensor" in alarm.message
    assert "Supply Temp" in alarm.message
    assert "Return Temp" in alarm.message


@pytest.mark.usefixtures("_check_db_available")
async def test_cross_sensor_rule_holds_no_alarm(
    db: TimescaleDBDatabase,
) -> None:
    """Kural sağlanıyorsa (A < B doğru) alarm yok."""
    tag_a = await _insert_tag(db, "TEST_R06_CROSS_OK_A", name="Supply Temp")
    tag_b = await _insert_tag(db, "TEST_R06_CROSS_OK_B", name="Return Temp")
    assert tag_a.id is not None and tag_b.id is not None

    rule = await db.insert_cross_sensor_rule(
        CrossSensorRule(
            name="TEST_R06_holds_check",
            tag_a_id=tag_a.id,
            tag_b_id=tag_b.id,
            operator="lt",
            severity="warn",
        ),
    )
    assert rule.id is not None

    now = datetime.now(UTC)
    # supply 18.0 < return 22.0 — kural sağlanıyor.
    await _write_reading(db, tag_a.tag_id, 18.0, now - timedelta(seconds=2))
    await _write_reading(db, tag_b.tag_id, 22.0, now - timedelta(seconds=2))

    engine = ThresholdEngine(db=db, check_interval_seconds=1.0)
    await engine._check_cycle()

    alarms = await db.list_alarm_events(
        tag_id=tag_a.tag_id, source="cross_sensor", limit=10,
    )
    assert len(alarms) == 0


@pytest.mark.usefixtures("_check_db_available")
async def test_cross_sensor_disabled_rule_skipped(
    db: TimescaleDBDatabase,
) -> None:
    """enabled=False kural değerlendirilmez."""
    tag_a = await _insert_tag(db, "TEST_R06_CROSS_OFF_A")
    tag_b = await _insert_tag(db, "TEST_R06_CROSS_OFF_B")
    assert tag_a.id is not None and tag_b.id is not None

    rule = await db.insert_cross_sensor_rule(
        CrossSensorRule(
            name="TEST_R06_disabled",
            tag_a_id=tag_a.id,
            tag_b_id=tag_b.id,
            operator="lt",
            severity="warn",
            enabled=False,
        ),
    )
    assert rule.id is not None

    now = datetime.now(UTC)
    # ihlal: A=10 < B=5 false
    await _write_reading(db, tag_a.tag_id, 10.0, now - timedelta(seconds=2))
    await _write_reading(db, tag_b.tag_id, 5.0, now - timedelta(seconds=2))

    engine = ThresholdEngine(db=db, check_interval_seconds=1.0)
    await engine._check_cycle()

    alarms = await db.list_alarm_events(
        tag_id=tag_a.tag_id, source="cross_sensor", limit=10,
    )
    assert len(alarms) == 0


@pytest.mark.usefixtures("_check_db_available")
async def test_cross_sensor_cooldown_prevents_duplicate(
    db: TimescaleDBDatabase,
) -> None:
    """Aynı kural cooldown süresi içinde yeniden alarm üretmemeli."""
    tag_a = await _insert_tag(db, "TEST_R06_CD_A")
    tag_b = await _insert_tag(db, "TEST_R06_CD_B")
    assert tag_a.id is not None and tag_b.id is not None

    rule = await db.insert_cross_sensor_rule(
        CrossSensorRule(
            name="TEST_R06_cooldown",
            tag_a_id=tag_a.id,
            tag_b_id=tag_b.id,
            operator="lt",
            severity="warn",
        ),
    )
    assert rule.id is not None

    now = datetime.now(UTC)
    await _write_reading(db, tag_a.tag_id, 30.0, now - timedelta(seconds=2))
    await _write_reading(db, tag_b.tag_id, 20.0, now - timedelta(seconds=2))

    engine = ThresholdEngine(db=db, check_interval_seconds=1.0)
    await engine._check_cycle()
    # İhlal devam — yeniden okuma ekle.
    await _write_reading(db, tag_a.tag_id, 31.0, now + timedelta(seconds=1))
    await _write_reading(db, tag_b.tag_id, 19.0, now + timedelta(seconds=1))
    await engine._check_cycle()

    alarms = await db.list_alarm_events(
        tag_id=tag_a.tag_id, source="cross_sensor", limit=10,
    )
    # 10 dk cooldown — tek alarm kalır.
    assert len(alarms) == 1
