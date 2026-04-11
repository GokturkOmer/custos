"""Threshold CRUD entegrasyon testleri.

TimescaleDB'nin ayakta olmasını gerektirir (docker compose up -d).
"""

from __future__ import annotations

import asyncio

import pytest

from custos.shared.config import Settings
from custos.shared.database import TagRecord, Threshold, TimescaleDBDatabase


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
        await conn.execute("DELETE FROM thresholds WHERE tag_id LIKE 'TEST_%'")
        await conn.execute("DELETE FROM tags WHERE tag_id LIKE 'TEST_%'")
    yield database  # type: ignore[misc]
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM thresholds WHERE tag_id LIKE 'TEST_%'")
        await conn.execute("DELETE FROM tags WHERE tag_id LIKE 'TEST_%'")
    await database.close()


async def _ensure_test_tag(db: TimescaleDBDatabase, suffix: str = "THR") -> TagRecord:
    """Threshold testleri için gerekli tag'i oluşturur."""
    existing = await db.get_tag(f"TEST_{suffix}")
    if existing is not None:
        return existing
    return await db.insert_tag(
        TagRecord(
            tag_id=f"TEST_{suffix}",
            name=f"Test Tag {suffix}",
            modbus_host="127.0.0.1",
            register_address=40001,
        ),
    )


@pytest.mark.usefixtures("_check_db_available")
async def test_insert_threshold(db: TimescaleDBDatabase) -> None:
    """Threshold oluşturup geri okunabiliyor mu?"""
    await _ensure_test_tag(db, "THR")
    t = Threshold(
        tag_id="TEST_THR",
        name="Yüksek Sıcaklık",
        direction="high",
        set_point=80.0,
        severity="crit",
    )
    created = await db.insert_threshold(t)

    assert created.id is not None
    assert created.tag_id == "TEST_THR"
    assert created.set_point == 80.0

    fetched = await db.get_threshold(created.id)
    assert fetched is not None
    assert fetched.name == "Yüksek Sıcaklık"


@pytest.mark.usefixtures("_check_db_available")
async def test_update_threshold(db: TimescaleDBDatabase) -> None:
    """Threshold güncellemesi çalışıyor mu?"""
    await _ensure_test_tag(db, "THR")
    created = await db.insert_threshold(
        Threshold(tag_id="TEST_THR", name="Update Test", set_point=50.0),
    )
    assert created.id is not None

    updated = await db.update_threshold(created.id, {"set_point": 90.0, "severity": "crit"})
    assert updated is not None
    assert updated.set_point == 90.0
    assert updated.severity == "crit"


@pytest.mark.usefixtures("_check_db_available")
async def test_delete_threshold(db: TimescaleDBDatabase) -> None:
    """Threshold silme çalışıyor mu?"""
    await _ensure_test_tag(db, "THR")
    created = await db.insert_threshold(
        Threshold(tag_id="TEST_THR", name="Delete Test", set_point=50.0),
    )
    assert created.id is not None

    assert await db.delete_threshold(created.id) is True
    assert await db.get_threshold(created.id) is None
    assert await db.delete_threshold(999999) is False


@pytest.mark.usefixtures("_check_db_available")
async def test_list_thresholds_filter_by_tag(db: TimescaleDBDatabase) -> None:
    """Tag'e göre filtreleme çalışıyor mu?"""
    await _ensure_test_tag(db, "THR")
    await _ensure_test_tag(db, "THR2")

    await db.insert_threshold(
        Threshold(tag_id="TEST_THR", name="Filter A", set_point=10.0),
    )
    await db.insert_threshold(
        Threshold(tag_id="TEST_THR2", name="Filter B", set_point=20.0),
    )

    filtered = await db.list_thresholds(tag_id="TEST_THR")
    assert all(t.tag_id == "TEST_THR" for t in filtered)
    assert any(t.name == "Filter A" for t in filtered)


@pytest.mark.usefixtures("_check_db_available")
async def test_list_thresholds_filter_by_enabled(db: TimescaleDBDatabase) -> None:
    """Enabled filtresi çalışıyor mu?"""
    await _ensure_test_tag(db, "THR")

    created = await db.insert_threshold(
        Threshold(tag_id="TEST_THR", name="Enabled Test", set_point=10.0, enabled=True),
    )
    assert created.id is not None

    await db.update_threshold(created.id, {"enabled": False})

    enabled = await db.list_thresholds(enabled=True)
    assert all(t.enabled for t in enabled)

    disabled = await db.list_thresholds(enabled=False)
    disabled_test = [t for t in disabled if t.tag_id.startswith("TEST_")]
    assert len(disabled_test) >= 1


@pytest.mark.usefixtures("_check_db_available")
async def test_update_threshold_rejects_invalid_fields(
    db: TimescaleDBDatabase,
) -> None:
    """Bilinmeyen alan güncellenmeye çalışılırsa ValueError fırlatılır."""
    await _ensure_test_tag(db, "THR")
    created = await db.insert_threshold(
        Threshold(tag_id="TEST_THR", name="Invalid Test", set_point=10.0),
    )
    assert created.id is not None

    with pytest.raises(ValueError, match="Güncellenemeyen alanlar"):
        await db.update_threshold(created.id, {"nonexistent_field": "value"})
