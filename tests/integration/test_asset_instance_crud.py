"""Asset instance CRUD entegrasyon testleri.

TimescaleDB'nin ayakta olmasını gerektirir (docker compose up -d).
"""

from __future__ import annotations

import asyncio

import pytest

from custos.shared.config import Settings
from custos.shared.database import AssetInstance, TagBinding, TimescaleDBDatabase


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
    # Test öncesi temizlik
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM tag_bindings WHERE instance_id IN "
            "(SELECT id FROM asset_instances WHERE name LIKE 'TEST_%')"
        )
        await conn.execute("DELETE FROM asset_instances WHERE name LIKE 'TEST_%'")
    yield database  # type: ignore[misc]
    # Test sonrası temizlik
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM tag_bindings WHERE instance_id IN "
            "(SELECT id FROM asset_instances WHERE name LIKE 'TEST_%')"
        )
        await conn.execute("DELETE FROM asset_instances WHERE name LIKE 'TEST_%'")
    await database.close()


async def _get_pump_template_id(db: TimescaleDBDatabase) -> int:
    """Pump template'inin id'sini döndürür."""
    templates = await db.list_asset_templates()
    pump = next(t for t in templates if t.slug == "pump")
    assert pump.id is not None
    return pump.id


@pytest.mark.usefixtures("_check_db_available")
async def test_insert_instance(db: TimescaleDBDatabase) -> None:
    """Asset instance oluşturulup okunabiliyor mu?"""
    tmpl_id = await _get_pump_template_id(db)
    instance = AssetInstance(
        template_id=tmpl_id,
        name="TEST_Pompa_001",
        location="Test Bölge",
    )
    created = await db.insert_asset_instance(instance)
    assert created.id is not None
    assert created.name == "TEST_Pompa_001"
    assert created.status == "active"

    fetched = await db.get_asset_instance(created.id)
    assert fetched is not None
    assert fetched.location == "Test Bölge"


@pytest.mark.usefixtures("_check_db_available")
async def test_update_instance(db: TimescaleDBDatabase) -> None:
    """Asset instance güncellemesi çalışıyor mu?"""
    tmpl_id = await _get_pump_template_id(db)
    created = await db.insert_asset_instance(
        AssetInstance(
            template_id=tmpl_id,
            name="TEST_Pompa_UPD",
        )
    )
    assert created.id is not None

    updated = await db.update_asset_instance(
        created.id,
        {
            "name": "TEST_Pompa_Updated",
            "location": "Yeni Konum",
            "status": "inactive",
        },
    )
    assert updated is not None
    assert updated.name == "TEST_Pompa_Updated"
    assert updated.location == "Yeni Konum"
    assert updated.status == "inactive"

    # Var olmayan instance
    result = await db.update_asset_instance(99999, {"name": "X"})
    assert result is None


@pytest.mark.usefixtures("_check_db_available")
async def test_delete_instance(db: TimescaleDBDatabase) -> None:
    """Asset instance silme çalışıyor mu?"""
    tmpl_id = await _get_pump_template_id(db)
    created = await db.insert_asset_instance(
        AssetInstance(
            template_id=tmpl_id,
            name="TEST_Pompa_DEL",
        )
    )
    assert created.id is not None

    assert await db.delete_asset_instance(created.id) is True
    assert await db.get_asset_instance(created.id) is None

    # Var olmayan instance
    assert await db.delete_asset_instance(99999) is False


@pytest.mark.usefixtures("_check_db_available")
async def test_list_instances_filter_by_template(db: TimescaleDBDatabase) -> None:
    """Template ve status filtreleri çalışıyor mu?"""
    tmpl_id = await _get_pump_template_id(db)
    await db.insert_asset_instance(
        AssetInstance(
            template_id=tmpl_id,
            name="TEST_Pompa_F1",
        )
    )
    inst2 = AssetInstance(
        template_id=tmpl_id,
        name="TEST_Pompa_F2",
        status="inactive",
    )
    await db.insert_asset_instance(inst2)

    by_template = await db.list_asset_instances(template_id=tmpl_id)
    test_insts = [i for i in by_template if i.name.startswith("TEST_")]
    assert len(test_insts) >= 2

    active_only = await db.list_asset_instances(template_id=tmpl_id, status="active")
    active_test = [i for i in active_only if i.name.startswith("TEST_")]
    assert all(i.status == "active" for i in active_test)


@pytest.mark.usefixtures("_check_db_available")
async def test_delete_instance_cascades_bindings(db: TimescaleDBDatabase) -> None:
    """Instance silinince binding'ler de CASCADE ile silinmeli."""
    tmpl_id = await _get_pump_template_id(db)
    tmpl = await db.get_asset_template(tmpl_id)
    assert tmpl is not None and len(tmpl.roles) > 0

    created = await db.insert_asset_instance(
        AssetInstance(
            template_id=tmpl_id,
            name="TEST_Pompa_CASCADE",
        )
    )
    assert created.id is not None

    # Binding oluşturmak için test tag lazım — önce tag oluştur
    from custos.shared.database import TagRecord

    tag = TagRecord(
        tag_id="TEST_CASCADE_TAG",
        name="Cascade Test Tag",
        modbus_host="127.0.0.1",
        register_address=0,
    )
    try:
        await db.insert_tag(tag)
    except Exception:
        pass  # Zaten varsa devam et

    role = tmpl.roles[0]
    assert role.id is not None
    binding = TagBinding(
        instance_id=created.id,
        role_id=role.id,
        tag_id="TEST_CASCADE_TAG",
    )
    await db.insert_tag_binding(binding)

    bindings = await db.list_tag_bindings(created.id)
    assert len(bindings) >= 1

    # Instance'ı sil — binding'ler CASCADE ile gitmeli
    await db.delete_asset_instance(created.id)

    pool = db._get_pool()
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM tag_bindings WHERE instance_id = $1",
            created.id,
        )
    assert count == 0

    # Temizlik
    await db.delete_tag("TEST_CASCADE_TAG")
