"""F9 AVM Template Pack integration testleri.

DB bağlıyken ``upsert_asset_template`` idempotent davranışı + YAML seed
runner'ın gerçek PG + TimescaleDB üzerine 9 şablonu yüklediğini doğrular.
``scripts/seed_asset_templates.py`` yeniden çalıştırıldığında orphan
bindings korunmalı (cascade davranışı).
"""

from __future__ import annotations

import asyncio

import pytest

from custos.analytics.templates import default_template_dir, load_templates
from custos.shared.config import Settings
from custos.shared.database import (
    AssetTemplate,
    KpiDefinition,
    TemplateRole,
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
    """Test için DB bağlantısı oluşturur."""
    s = Settings()
    database = TimescaleDBDatabase(s)
    await database.connect()
    yield database  # type: ignore[misc]
    await database.close()


@pytest.mark.usefixtures("_check_db_available")
async def test_upsert_inserts_new_template(db: TimescaleDBDatabase) -> None:
    """Yeni slug için upsert insert yapar ve roller/KPI'ları döndürür."""
    slug = "_f9_test_new_asset"
    tmpl = AssetTemplate(
        slug=slug,
        name="F9 Test Asset",
        description="Integration test fixture",
        icon="activity",
    )
    tmpl.roles = [
        TemplateRole(
            template_id=0,
            role_key="inlet",
            label="Giriş",
            unit_hint="°C",
            required=True,
            sort_order=1,
        ),
        TemplateRole(
            template_id=0,
            role_key="outlet",
            label="Çıkış",
            unit_hint="°C",
            required=True,
            sort_order=2,
        ),
    ]
    tmpl.kpi_definitions = [
        KpiDefinition(
            template_id=0, name="delta", formula="outlet - inlet", unit="°C", description="Fark"
        ),
    ]

    try:
        result = await db.upsert_asset_template(tmpl)
        assert result.id is not None
        assert result.slug == slug
        assert len(result.roles) == 2
        assert {r.role_key for r in result.roles} == {"inlet", "outlet"}
        assert len(result.kpi_definitions) == 1
        assert result.kpi_definitions[0].name == "delta"
    finally:
        # Cleanup
        pool = db._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM asset_templates WHERE slug = $1",
                slug,
            )


@pytest.mark.usefixtures("_check_db_available")
async def test_upsert_updates_existing_template(db: TimescaleDBDatabase) -> None:
    """Aynı slug ikinci kez upsert edilince güncellenir (insert değil)."""
    slug = "_f9_test_update_asset"
    first = AssetTemplate(
        slug=slug,
        name="İlk İsim",
        description="v1",
        icon="cpu",
    )
    first.roles = [
        TemplateRole(
            template_id=0,
            role_key="speed",
            label="Hız",
            unit_hint="rpm",
            required=True,
            sort_order=1,
        ),
    ]

    second = AssetTemplate(
        slug=slug,
        name="İkinci İsim",
        description="v2",
        icon="activity",
    )
    second.roles = [
        TemplateRole(
            template_id=0,
            role_key="speed",
            label="Hız (güncel)",
            unit_hint="rpm",
            required=True,
            sort_order=1,
        ),
        TemplateRole(
            template_id=0,
            role_key="torque",
            label="Tork",
            unit_hint="Nm",
            required=False,
            sort_order=2,
        ),
    ]

    try:
        r1 = await db.upsert_asset_template(first)
        r2 = await db.upsert_asset_template(second)

        # Aynı id, güncellenmiş name/description
        assert r1.id == r2.id
        assert r2.name == "İkinci İsim"
        assert r2.description == "v2"
        assert r2.icon == "activity"

        # Roller: speed güncellendi (label değişti), torque yeni eklendi
        role_map = {r.role_key: r for r in r2.roles}
        assert role_map["speed"].label == "Hız (güncel)"
        assert "torque" in role_map
    finally:
        pool = db._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM asset_templates WHERE slug = $1",
                slug,
            )


@pytest.mark.usefixtures("_check_db_available")
async def test_seed_script_loads_all_nine_avm_templates(
    db: TimescaleDBDatabase,
) -> None:
    """scripts/seed_asset_templates.py mantığı 9 F9 şablonunu DB'ye yüklemeli."""
    loaded = load_templates(default_template_dir())
    slugs = [entry.schema.slug for entry in loaded]
    assert len(slugs) == 9

    # Seed — seed_asset_templates.py ile aynı akış
    for entry in loaded:
        await db.upsert_asset_template(entry.schema.to_asset_template())

    # DB'den tekrar oku
    templates = await db.list_asset_templates()
    db_slugs = {t.slug for t in templates}

    for slug in slugs:
        assert slug in db_slugs, f"Seed sonrası DB'de eksik: {slug}"

    # Chiller'ın KPI + rol sayılarını doğrula
    chiller = next(t for t in templates if t.slug == "chiller")
    assert len(chiller.roles) >= 3  # en az zorunlu 3
    assert len(chiller.kpi_definitions) >= 2


@pytest.mark.usefixtures("_check_db_available")
async def test_seed_is_idempotent_on_repeat(db: TimescaleDBDatabase) -> None:
    """Aynı seed ikinci kez çalıştırılınca satır sayısı değişmemeli."""
    loaded = load_templates(default_template_dir())

    for entry in loaded:
        await db.upsert_asset_template(entry.schema.to_asset_template())
    first_count = len(await db.list_asset_templates())

    for entry in loaded:
        await db.upsert_asset_template(entry.schema.to_asset_template())
    second_count = len(await db.list_asset_templates())

    assert first_count == second_count, "Idempotent değil — seed template satır sayısını artırıyor"
