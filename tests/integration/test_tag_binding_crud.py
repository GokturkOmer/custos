"""Tag binding CRUD entegrasyon testleri.

TimescaleDB'nin ayakta olmasını gerektirir (docker compose up -d).
"""

from __future__ import annotations

import asyncio

import pytest

from custos.shared.config import Settings
from custos.shared.database import (
    AssetInstance,
    TagBinding,
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
    """Test için DB bağlantısı oluşturur ve temizler."""
    s = Settings()
    database = TimescaleDBDatabase(s)
    await database.connect()
    pool = database._get_pool()
    # Test öncesi temizlik
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM tag_bindings WHERE instance_id IN "
                           "(SELECT id FROM asset_instances WHERE name LIKE 'TEST_%')")
        await conn.execute("DELETE FROM asset_instances WHERE name LIKE 'TEST_%'")
        await conn.execute("DELETE FROM tags WHERE tag_id LIKE 'TEST_BIND_%'")
    yield database  # type: ignore[misc]
    # Test sonrası temizlik
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM tag_bindings WHERE instance_id IN "
                           "(SELECT id FROM asset_instances WHERE name LIKE 'TEST_%')")
        await conn.execute("DELETE FROM asset_instances WHERE name LIKE 'TEST_%'")
        await conn.execute("DELETE FROM tags WHERE tag_id LIKE 'TEST_BIND_%'")
    await database.close()


async def _setup_binding_fixtures(
    db: TimescaleDBDatabase,
) -> tuple[int, list[int], list[str]]:
    """Test için instance, role id'leri ve tag_id'ler oluşturur.

    Returns: (instance_id, [role_id, ...], [tag_id, ...])
    """
    # Pump template al
    templates = await db.list_asset_templates()
    pump = next(t for t in templates if t.slug == "pump")
    assert pump.id is not None

    # Instance oluştur
    instance = await db.insert_asset_instance(AssetInstance(
        template_id=pump.id, name="TEST_Binding_Instance",
    ))
    assert instance.id is not None

    # Test tag'leri oluştur
    tag_ids: list[str] = []
    for i in range(len(pump.roles)):
        tid = f"TEST_BIND_{i:03d}"
        tag = TagRecord(
            tag_id=tid, name=f"Bind Tag {i}",
            modbus_host="127.0.0.1", register_address=i,
        )
        try:
            await db.insert_tag(tag)
        except Exception:
            pass  # Zaten varsa devam et
        tag_ids.append(tid)

    role_ids = [r.id for r in pump.roles if r.id is not None]
    return instance.id, role_ids, tag_ids


@pytest.mark.usefixtures("_check_db_available")
async def test_insert_binding(db: TimescaleDBDatabase) -> None:
    """Tag binding oluşturulup okunabiliyor mu?"""
    instance_id, role_ids, tag_ids = await _setup_binding_fixtures(db)

    binding = TagBinding(
        instance_id=instance_id,
        role_id=role_ids[0],
        tag_id=tag_ids[0],
    )
    created = await db.insert_tag_binding(binding)
    assert created.id is not None
    assert created.instance_id == instance_id
    assert created.tag_id == tag_ids[0]


@pytest.mark.usefixtures("_check_db_available")
async def test_replace_bindings(db: TimescaleDBDatabase) -> None:
    """replace_tag_bindings mevcut binding'leri silip yenileriyle değiştirmeli."""
    instance_id, role_ids, tag_ids = await _setup_binding_fixtures(db)

    # İlk set
    first_bindings = [
        TagBinding(instance_id=instance_id, role_id=role_ids[0], tag_id=tag_ids[0]),
    ]
    result1 = await db.replace_tag_bindings(instance_id, first_bindings)
    assert len(result1) == 1

    # İkinci set — iki binding
    second_bindings = [
        TagBinding(instance_id=instance_id, role_id=role_ids[0], tag_id=tag_ids[1]),
        TagBinding(instance_id=instance_id, role_id=role_ids[1], tag_id=tag_ids[0]),
    ]
    result2 = await db.replace_tag_bindings(instance_id, second_bindings)
    assert len(result2) == 2

    # Eski binding gitmiş olmalı, yeni ikisi olmalı
    current = await db.list_tag_bindings(instance_id)
    assert len(current) == 2
    current_tags = {b.tag_id for b in current}
    assert tag_ids[0] in current_tags
    assert tag_ids[1] in current_tags


@pytest.mark.usefixtures("_check_db_available")
async def test_list_bindings_for_instance(db: TimescaleDBDatabase) -> None:
    """Instance'a ait binding'ler listelenebilmeli."""
    instance_id, role_ids, tag_ids = await _setup_binding_fixtures(db)

    # Boş başlamalı
    empty = await db.list_tag_bindings(instance_id)
    assert len(empty) == 0

    # Binding ekle
    await db.insert_tag_binding(TagBinding(
        instance_id=instance_id, role_id=role_ids[0], tag_id=tag_ids[0],
    ))
    after = await db.list_tag_bindings(instance_id)
    assert len(after) == 1


@pytest.mark.usefixtures("_check_db_available")
async def test_delete_binding(db: TimescaleDBDatabase) -> None:
    """Tag binding silme çalışıyor mu?"""
    instance_id, role_ids, tag_ids = await _setup_binding_fixtures(db)

    created = await db.insert_tag_binding(TagBinding(
        instance_id=instance_id, role_id=role_ids[0], tag_id=tag_ids[0],
    ))
    assert created.id is not None

    assert await db.delete_tag_binding(created.id) is True
    assert await db.delete_tag_binding(created.id) is False  # Zaten silindi

    remaining = await db.list_tag_bindings(instance_id)
    assert len(remaining) == 0
