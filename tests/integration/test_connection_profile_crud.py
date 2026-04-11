"""Connection Profile CRUD entegrasyon testleri.

TimescaleDB'nin ayakta olmasını gerektirir (docker compose up -d).
"""

from __future__ import annotations

import asyncio

import pytest

from custos.shared.config import Settings
from custos.shared.database import ConnectionProfile, TimescaleDBDatabase


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
        await conn.execute(
            "DELETE FROM connection_profiles WHERE name LIKE 'TEST_%'"
        )
    yield database  # type: ignore[misc]
    # Test sonrası temizlik
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM connection_profiles WHERE name LIKE 'TEST_%'"
        )
    await database.close()


def _make_test_profile(suffix: str = "001") -> ConnectionProfile:
    """Test için ConnectionProfile oluşturur."""
    return ConnectionProfile(
        name=f"TEST_{suffix}",
        host="127.0.0.1",
        port=5020,
        unit_id_start=1,
        unit_id_end=1,
    )


@pytest.mark.usefixtures("_check_db_available")
async def test_insert_and_get_profile(db: TimescaleDBDatabase) -> None:
    """Profil oluşturup geri okunabiliyor mu?"""
    profile = _make_test_profile("001")
    created = await db.insert_connection_profile(profile)

    assert created.name == "TEST_001"
    assert created.host == "127.0.0.1"
    assert created.port == 5020
    assert created.id is not None
    assert created.status == "idle"

    fetched = await db.get_connection_profile(created.id)
    assert fetched is not None
    assert fetched.name == "TEST_001"
    assert fetched.host == "127.0.0.1"


@pytest.mark.usefixtures("_check_db_available")
async def test_list_profiles(db: TimescaleDBDatabase) -> None:
    """Profil listesi doğru dönüyor mu?"""
    await db.insert_connection_profile(_make_test_profile("L01"))
    await db.insert_connection_profile(_make_test_profile("L02"))

    all_profiles = await db.list_connection_profiles()
    test_profiles = [p for p in all_profiles if p.name.startswith("TEST_")]
    assert len(test_profiles) >= 2


@pytest.mark.usefixtures("_check_db_available")
async def test_update_profile(db: TimescaleDBDatabase) -> None:
    """Profil güncellemesi çalışıyor mu?"""
    created = await db.insert_connection_profile(_make_test_profile("UPD"))
    assert created.id is not None

    updated = await db.update_connection_profile(
        created.id, {"name": "TEST_UPD_RENAMED", "port": 5021}
    )
    assert updated is not None
    assert updated.name == "TEST_UPD_RENAMED"
    assert updated.port == 5021

    # Var olmayan profil
    result = await db.update_connection_profile(99999, {"name": "X"})
    assert result is None


@pytest.mark.usefixtures("_check_db_available")
async def test_update_profile_rejects_invalid_fields(
    db: TimescaleDBDatabase,
) -> None:
    """Bilinmeyen alan güncellenmeye çalışılırsa ValueError fırlatılır."""
    created = await db.insert_connection_profile(_make_test_profile("INV"))
    assert created.id is not None

    with pytest.raises(ValueError, match="Güncellenemeyen alanlar"):
        await db.update_connection_profile(
            created.id, {"nonexistent_field": "value"}
        )


@pytest.mark.usefixtures("_check_db_available")
async def test_delete_profile(db: TimescaleDBDatabase) -> None:
    """Profil silme çalışıyor mu?"""
    created = await db.insert_connection_profile(_make_test_profile("DEL"))
    assert created.id is not None

    assert await db.delete_connection_profile(created.id) is True
    assert await db.get_connection_profile(created.id) is None

    # Var olmayan profil
    assert await db.delete_connection_profile(99999) is False
