"""Threshold Engine entegrasyon testleri.

TimescaleDB'nin ayakta olmasını gerektirir (docker compose up -d).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from custos.analytics.threshold_engine import ThresholdEngine
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


@pytest.fixture
async def db() -> TimescaleDBDatabase:
    """Test için DB bağlantısı oluşturur ve temizler."""
    s = Settings()
    database = TimescaleDBDatabase(s)
    await database.connect()
    pool = database._get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM audit_log WHERE entity_id LIKE 'TEST_%'")
        await conn.execute("DELETE FROM alarm_events WHERE tag_id LIKE 'TEST_%'")
        await conn.execute("DELETE FROM thresholds WHERE tag_id LIKE 'TEST_%'")
        await conn.execute("DELETE FROM tag_readings WHERE tag_id LIKE 'TEST_%'")
        await conn.execute("DELETE FROM tags WHERE tag_id LIKE 'TEST_%'")
    yield database  # type: ignore[misc]
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM audit_log WHERE entity_id LIKE 'TEST_%'")
        await conn.execute("DELETE FROM alarm_events WHERE tag_id LIKE 'TEST_%'")
        await conn.execute("DELETE FROM thresholds WHERE tag_id LIKE 'TEST_%'")
        await conn.execute("DELETE FROM tag_readings WHERE tag_id LIKE 'TEST_%'")
        await conn.execute("DELETE FROM tags WHERE tag_id LIKE 'TEST_%'")
    await database.close()


async def _setup_tag_with_reading(
    db: TimescaleDBDatabase,
    tag_id: str,
    value: float,
) -> None:
    """Tag oluşturur ve bir okuma ekler."""
    existing = await db.get_tag(tag_id)
    if existing is None:
        await db.insert_tag(
            TagRecord(
                tag_id=tag_id,
                name=f"Test {tag_id}",
                modbus_host="127.0.0.1",
                register_address=40001,
            ),
        )
    now = datetime.now(UTC)
    await db.insert_tag_readings_batch(
        [
            TagReading(timestamp=now, tag_id=tag_id, value=value),
        ]
    )


@pytest.mark.usefixtures("_check_db_available")
async def test_engine_triggers_alarm_on_high_breach(
    db: TimescaleDBDatabase,
) -> None:
    """Eşik aşımında alarm tetikleniyor mu?"""
    await _setup_tag_with_reading(db, "TEST_ENG1", 90.0)
    threshold = await db.insert_threshold(
        Threshold(
            tag_id="TEST_ENG1",
            name="High Temp Test",
            direction="high",
            set_point=80.0,
            debounce_seconds=0,  # Debounce yok — anında tetikle
        ),
    )
    assert threshold.id is not None

    engine = ThresholdEngine(db=db, check_interval_seconds=1.0)
    await engine._check_cycle()

    # Debounce 0 ama ilk çağrıda tracker'a yazılır, ikinci çağrıda tetiklenir
    await engine._check_cycle()

    active = await db.get_active_alarm_for_threshold(threshold.id)
    assert active is not None
    assert active.state == "triggered"
    assert active.trigger_value == 90.0


@pytest.mark.usefixtures("_check_db_available")
async def test_engine_respects_debounce(db: TimescaleDBDatabase) -> None:
    """Debounce süresi dolmadan alarm tetiklenmemeli."""
    await _setup_tag_with_reading(db, "TEST_ENG2", 90.0)
    threshold = await db.insert_threshold(
        Threshold(
            tag_id="TEST_ENG2",
            name="Debounce Test",
            direction="high",
            set_point=80.0,
            debounce_seconds=60,  # 60 saniye — hiç dolmayacak
        ),
    )
    assert threshold.id is not None

    engine = ThresholdEngine(db=db, check_interval_seconds=1.0)
    await engine._check_cycle()
    await engine._check_cycle()

    active = await db.get_active_alarm_for_threshold(threshold.id)
    assert active is None  # Debounce dolmadı


@pytest.mark.usefixtures("_check_db_available")
async def test_engine_clears_alarm_with_hysteresis(
    db: TimescaleDBDatabase,
) -> None:
    """Hysteresis bandı doğru çalışıyor mu?"""
    await _setup_tag_with_reading(db, "TEST_ENG3", 90.0)
    threshold = await db.insert_threshold(
        Threshold(
            tag_id="TEST_ENG3",
            name="Hysteresis Test",
            direction="high",
            set_point=80.0,
            debounce_seconds=0,
            hysteresis=5.0,  # 80 - 5 = 75 altına düşmeli
        ),
    )
    assert threshold.id is not None

    engine = ThresholdEngine(db=db, check_interval_seconds=1.0)
    # Alarm tetikle (debounce=0, iki cycle)
    await engine._check_cycle()
    await engine._check_cycle()

    active = await db.get_active_alarm_for_threshold(threshold.id)
    assert active is not None

    # Değeri 77'ye düşür (hâlâ hysteresis bandında: 80-5=75'in üstünde)
    await _setup_tag_with_reading(db, "TEST_ENG3", 77.0)
    await engine._check_cycle()

    still_active = await db.get_active_alarm_for_threshold(threshold.id)
    assert still_active is not None  # Temizlenmedi

    # Değeri 74'e düşür (hysteresis bandının altına: 80-5=75)
    await _setup_tag_with_reading(db, "TEST_ENG3", 74.0)
    await engine._check_cycle()

    cleared = await db.get_active_alarm_for_threshold(threshold.id)
    assert cleared is None  # Temizlendi


@pytest.mark.usefixtures("_check_db_available")
async def test_engine_ignores_disabled_threshold(
    db: TimescaleDBDatabase,
) -> None:
    """Pasif threshold'lar değerlendirilmemeli."""
    await _setup_tag_with_reading(db, "TEST_ENG4", 90.0)
    threshold = await db.insert_threshold(
        Threshold(
            tag_id="TEST_ENG4",
            name="Disabled Test",
            direction="high",
            set_point=80.0,
            debounce_seconds=0,
            enabled=False,
        ),
    )
    assert threshold.id is not None

    engine = ThresholdEngine(db=db, check_interval_seconds=1.0)
    await engine._check_cycle()
    await engine._check_cycle()

    active = await db.get_active_alarm_for_threshold(threshold.id)
    assert active is None  # Pasif olduğu için değerlendirilmedi


@pytest.mark.usefixtures("_check_db_available")
async def test_engine_low_direction_breach_and_clear(
    db: TimescaleDBDatabase,
) -> None:
    """Low direction (alt eşik) breach + hysteresis clear yolu çalışmalı.

    Mevcut testler high direction kapsıyor; low direction _is_breach ve
    _can_clear_with_hysteresis dallarını ayrıca kapsar.
    """
    await _setup_tag_with_reading(db, "TEST_ENG5", 5.0)  # 10 altı = breach
    threshold = await db.insert_threshold(
        Threshold(
            tag_id="TEST_ENG5",
            name="Low Pressure Test",
            direction="low",
            set_point=10.0,
            debounce_seconds=0,
            hysteresis=2.0,  # 10 + 2 = 12 üstüne çıkmalı temizlemek için
        ),
    )
    assert threshold.id is not None

    engine = ThresholdEngine(db=db, check_interval_seconds=1.0)
    await engine._check_cycle()
    await engine._check_cycle()

    active = await db.get_active_alarm_for_threshold(threshold.id)
    assert active is not None
    assert active.trigger_value == 5.0

    # 11.0: breach yok ama hysteresis bandında (10+2=12 altında) → temizlenmemeli
    await _setup_tag_with_reading(db, "TEST_ENG5", 11.0)
    await engine._check_cycle()
    still = await db.get_active_alarm_for_threshold(threshold.id)
    assert still is not None  # Hysteresis bandında

    # 13.0: hysteresis bandının üstü → temizlenmeli
    await _setup_tag_with_reading(db, "TEST_ENG5", 13.0)
    await engine._check_cycle()
    cleared = await db.get_active_alarm_for_threshold(threshold.id)
    assert cleared is None


@pytest.mark.usefixtures("_check_db_available")
async def test_engine_clears_debounce_tracker_when_reading_disappears(
    db: TimescaleDBDatabase,
) -> None:
    """Debounce başladıktan sonra reading silinirse tracker temizlenmeli.

    State machine D→A→D geçişi: ilk cycle breach (tracker'a yazıldı),
    okuma silindi (Durum: tag için reading None) → tracker'dan düşmeli.
    """
    pool = db._get_pool()
    await _setup_tag_with_reading(db, "TEST_ENG6", 90.0)
    threshold = await db.insert_threshold(
        Threshold(
            tag_id="TEST_ENG6",
            name="Tracker Cleanup Test",
            direction="high",
            set_point=80.0,
            debounce_seconds=60,  # Hiç dolmayacak — tracker'da kalsın
        ),
    )
    assert threshold.id is not None

    engine = ThresholdEngine(db=db, check_interval_seconds=1.0)
    await engine._check_cycle()
    assert threshold.id in engine._debounce_tracker  # İlk breach kaydedildi

    # Reading'i sil (tag'in mevcut okuması yok artık)
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM tag_readings WHERE tag_id = $1", "TEST_ENG6")

    await engine._check_cycle()
    assert threshold.id not in engine._debounce_tracker  # Tracker temizlendi
    active = await db.get_active_alarm_for_threshold(threshold.id)
    assert active is None  # Alarm da tetiklenmemiş olmalı
