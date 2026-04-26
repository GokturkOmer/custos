"""Asset template entegrasyon testleri.

TimescaleDB'nin ayakta olmasını gerektirir (docker compose up -d).
Template'ler seed verisi olarak migration ile eklenir, bu testler
sadece read-only sorguları doğrular.
"""

from __future__ import annotations

import asyncio

import pytest

from custos.shared.config import Settings
from custos.shared.database import TimescaleDBDatabase


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
async def test_list_templates_returns_seed_data(db: TimescaleDBDatabase) -> None:
    """Migration seed verisi olarak en az 6 template mevcut olmalı."""
    templates = await db.list_asset_templates()
    assert len(templates) >= 6

    slugs = {t.slug for t in templates}
    expected = {
        "pump",
        "chiller",
        "plate_heat_exchanger",
        "air_compressor",
        "generic_motor",
        "generic_tank",
    }
    assert expected.issubset(slugs)


@pytest.mark.usefixtures("_check_db_available")
async def test_get_template_includes_roles_and_kpis(db: TimescaleDBDatabase) -> None:
    """Template detayı roller ve KPI tanımlarını içermeli."""
    templates = await db.list_asset_templates()
    pump = next(t for t in templates if t.slug == "pump")
    assert pump.id is not None

    detail = await db.get_asset_template(pump.id)
    assert detail is not None
    assert len(detail.roles) >= 4  # en az 4 zorunlu rol
    assert len(detail.kpi_definitions) >= 2  # specific_energy + differential_pressure

    role_keys = {r.role_key for r in detail.roles}
    assert "suction_pressure" in role_keys
    assert "motor_current" in role_keys

    kpi_names = {k.name for k in detail.kpi_definitions}
    assert "specific_energy" in kpi_names


@pytest.mark.usefixtures("_check_db_available")
async def test_get_nonexistent_template_returns_none(db: TimescaleDBDatabase) -> None:
    """Var olmayan template_id için None dönmeli."""
    result = await db.get_asset_template(99999)
    assert result is None
