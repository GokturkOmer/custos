"""Tag CRUD entegrasyon testleri.

TimescaleDB'nin ayakta olmasını gerektirir (docker compose up -d).
"""

from __future__ import annotations

import asyncio

import pytest

from custos.shared.config import Settings
from custos.shared.database import TagRecord, TimescaleDBDatabase


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
    # Test öncesi temizlik
    pool = database._get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM tags WHERE tag_id LIKE 'TEST_%'")
    yield database  # type: ignore[misc]
    # Test sonrası temizlik
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM tags WHERE tag_id LIKE 'TEST_%'")
    await database.close()


def _make_test_tag(suffix: str = "001") -> TagRecord:
    """Test için TagRecord oluşturur."""
    return TagRecord(
        tag_id=f"TEST_{suffix}",
        name=f"Test Tag {suffix}",
        modbus_host="127.0.0.1",
        modbus_port=502,
        unit_id=1,
        register_address=40001,
        register_type="uint16",
        gain=1.0,
        offset=0.0,
        unit="°C",
    )


@pytest.mark.usefixtures("_check_db_available")
async def test_insert_and_get_tag(db: TimescaleDBDatabase) -> None:
    """Tag oluşturup geri okunabiliyor mu?"""
    tag = _make_test_tag("001")
    created = await db.insert_tag(tag)

    assert created.tag_id == "TEST_001"
    assert created.name == "Test Tag 001"
    assert created.id is not None

    fetched = await db.get_tag("TEST_001")
    assert fetched is not None
    assert fetched.tag_id == "TEST_001"
    assert fetched.modbus_host == "127.0.0.1"
    assert fetched.gain == 1.0


@pytest.mark.usefixtures("_check_db_available")
async def test_list_tags(db: TimescaleDBDatabase) -> None:
    """Tag listesi doğru dönüyor mu?"""
    await db.insert_tag(_make_test_tag("A01"))
    tag2 = _make_test_tag("A02")
    tag2.status = "ignored"
    await db.insert_tag(tag2)

    all_tags = await db.list_tags()
    test_tags = [t for t in all_tags if t.tag_id.startswith("TEST_")]
    assert len(test_tags) >= 2

    active_tags = await db.list_tags(status="active")
    active_test = [t for t in active_tags if t.tag_id.startswith("TEST_")]
    assert all(t.status == "active" for t in active_test)

    ignored_tags = await db.list_tags(status="ignored")
    ignored_test = [t for t in ignored_tags if t.tag_id.startswith("TEST_")]
    assert len(ignored_test) >= 1


@pytest.mark.usefixtures("_check_db_available")
async def test_update_tag(db: TimescaleDBDatabase) -> None:
    """Tag güncellemesi çalışıyor mu?"""
    await db.insert_tag(_make_test_tag("UPD"))

    updated = await db.update_tag("TEST_UPD", {"name": "Updated Name", "gain": 2.5})
    assert updated is not None
    assert updated.name == "Updated Name"
    assert updated.gain == 2.5

    # Var olmayan tag
    result = await db.update_tag("TEST_NONEXISTENT", {"name": "X"})
    assert result is None


@pytest.mark.usefixtures("_check_db_available")
async def test_update_tag_rejects_invalid_fields(db: TimescaleDBDatabase) -> None:
    """Bilinmeyen alan güncellenmeye çalışılırsa ValueError fırlatılır."""
    await db.insert_tag(_make_test_tag("INV"))

    with pytest.raises(ValueError, match="Güncellenemeyen alanlar"):
        await db.update_tag("TEST_INV", {"nonexistent_field": "value"})


@pytest.mark.usefixtures("_check_db_available")
async def test_delete_tag(db: TimescaleDBDatabase) -> None:
    """Tag silme çalışıyor mu?"""
    await db.insert_tag(_make_test_tag("DEL"))

    assert await db.delete_tag("TEST_DEL") is True
    assert await db.get_tag("TEST_DEL") is None

    # Var olmayan tag
    assert await db.delete_tag("TEST_NONEXISTENT") is False
