"""KPI Result CRUD entegrasyon testleri.

TimescaleDB'nin ayakta olmasını gerektirir (docker compose up -d).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest

from custos.shared.config import Settings
from custos.shared.database import AssetInstance, KpiResult, TimescaleDBDatabase


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
async def db() -> AsyncIterator[tuple[TimescaleDBDatabase, int, int]]:
    """Test için DB bağlantısı, test instance ve kpi_definition id'leri oluşturur."""
    s = Settings()
    database = TimescaleDBDatabase(s)
    await database.connect()
    pool = database._get_pool()

    # Test için geçici asset_instance oluştur (template_id=1 seed'den var)
    inst = await database.insert_asset_instance(
        AssetInstance(template_id=1, name="TEST_KPI_INST"),
    )
    assert inst.id is not None

    # Template 1'in (Pump) KPI definition id'lerini al
    tmpl = await database.get_asset_template(1)
    assert tmpl is not None and len(tmpl.kpi_definitions) >= 2
    kd1_id = tmpl.kpi_definitions[0].id
    kd2_id = tmpl.kpi_definitions[1].id
    assert kd1_id is not None and kd2_id is not None

    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM kpi_results WHERE instance_id = $1",
            inst.id,
        )
    yield database, inst.id, kd1_id  # type: ignore[misc]
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM kpi_results WHERE instance_id = $1",
            inst.id,
        )
    await database.delete_asset_instance(inst.id)
    await database.close()


@pytest.mark.usefixtures("_check_db_available")
async def test_insert_kpi_result(db: tuple[TimescaleDBDatabase, int, int]) -> None:
    """KPI sonucu kaydedip geri okuyabilmeli."""
    database, inst_id, kd_id = db
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    result = KpiResult(instance_id=inst_id, kpi_definition_id=kd_id, bucket_start=now, value=42.5)
    saved = await database.insert_kpi_result(result)
    assert saved.id is not None
    assert saved.value == 42.5
    assert saved.instance_id == inst_id


@pytest.mark.usefixtures("_check_db_available")
async def test_insert_kpi_results_batch(db: tuple[TimescaleDBDatabase, int, int]) -> None:
    """Çoklu KPI sonucu batch halinde kaydedebilmeli."""
    database, inst_id, kd_id = db
    tmpl = await database.get_asset_template(1)
    assert tmpl is not None and len(tmpl.kpi_definitions) >= 2
    kd2_id = tmpl.kpi_definitions[1].id
    assert kd2_id is not None

    now = datetime.now(UTC).replace(second=0, microsecond=0)
    results = [
        KpiResult(instance_id=inst_id, kpi_definition_id=kd_id, bucket_start=now, value=10.0),
        KpiResult(instance_id=inst_id, kpi_definition_id=kd2_id, bucket_start=now, value=20.0),
    ]
    await database.insert_kpi_results_batch(results)
    fetched = await database.list_kpi_results(instance_id=inst_id)
    assert len(fetched) >= 2


@pytest.mark.usefixtures("_check_db_available")
async def test_list_kpi_results(db: tuple[TimescaleDBDatabase, int, int]) -> None:
    """KPI sonuçlarını filtreleyerek listeyebilmeli."""
    database, inst_id, kd_id = db
    tmpl = await database.get_asset_template(1)
    assert tmpl is not None and len(tmpl.kpi_definitions) >= 2
    kd2_id = tmpl.kpi_definitions[1].id
    assert kd2_id is not None

    now = datetime.now(UTC).replace(second=0, microsecond=0)
    await database.insert_kpi_result(
        KpiResult(instance_id=inst_id, kpi_definition_id=kd_id, bucket_start=now, value=5.0),
    )
    await database.insert_kpi_result(
        KpiResult(instance_id=inst_id, kpi_definition_id=kd2_id, bucket_start=now, value=15.0),
    )
    # Definition filtresi
    filtered = await database.list_kpi_results(instance_id=inst_id, kpi_definition_id=kd_id)
    assert len(filtered) == 1
    assert filtered[0].value == 5.0


@pytest.mark.usefixtures("_check_db_available")
async def test_get_latest_kpi_results(db: tuple[TimescaleDBDatabase, int, int]) -> None:
    """Her KPI definition için en son değeri döndürmeli."""
    database, inst_id, kd_id = db
    t1 = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    t2 = datetime(2026, 1, 1, 0, 1, tzinfo=UTC)
    await database.insert_kpi_result(
        KpiResult(instance_id=inst_id, kpi_definition_id=kd_id, bucket_start=t1, value=1.0),
    )
    await database.insert_kpi_result(
        KpiResult(instance_id=inst_id, kpi_definition_id=kd_id, bucket_start=t2, value=2.0),
    )
    latest = await database.get_latest_kpi_results(instance_id=inst_id)
    assert kd_id in latest
    assert latest[kd_id].value == 2.0
