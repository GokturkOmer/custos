"""Anomaly Score CRUD entegrasyon testleri.

TimescaleDB'nin ayakta olmasını gerektirir (docker compose up -d).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest

from custos.shared.config import Settings
from custos.shared.database import AnomalyScore, AssetInstance, TimescaleDBDatabase


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
async def db() -> AsyncIterator[tuple[TimescaleDBDatabase, int]]:
    """Test için DB bağlantısı ve test instance oluşturur."""
    s = Settings()
    database = TimescaleDBDatabase(s)
    await database.connect()

    # Test için geçici asset_instance oluştur
    inst = await database.insert_asset_instance(
        AssetInstance(template_id=1, name="TEST_ANOMALY_INST"),
    )
    assert inst.id is not None

    pool = database._get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM anomaly_scores WHERE instance_id = $1",
            inst.id,
        )
    yield database, inst.id  # type: ignore[misc]
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM anomaly_scores WHERE instance_id = $1",
            inst.id,
        )
    await database.delete_asset_instance(inst.id)
    await database.close()


@pytest.mark.usefixtures("_check_db_available")
async def test_insert_anomaly_score(db: tuple[TimescaleDBDatabase, int]) -> None:
    """Anomali skoru kaydedip geri okuyabilmeli."""
    database, inst_id = db
    now = datetime.now(UTC)
    score = AnomalyScore(
        instance_id=inst_id,
        timestamp=now,
        score=-0.15,
        is_anomaly=True,
        feature_vector='{"tag1": 42.0}',
    )
    saved = await database.insert_anomaly_score(score)
    assert saved.id is not None
    assert saved.is_anomaly is True
    assert saved.score == -0.15


@pytest.mark.usefixtures("_check_db_available")
async def test_list_anomaly_scores(db: tuple[TimescaleDBDatabase, int]) -> None:
    """Anomali skor listesini döndürmeli."""
    database, inst_id = db
    now = datetime.now(UTC)
    await database.insert_anomaly_score(
        AnomalyScore(instance_id=inst_id, timestamp=now, score=0.1, is_anomaly=False),
    )
    await database.insert_anomaly_score(
        AnomalyScore(instance_id=inst_id, timestamp=now, score=-0.2, is_anomaly=True),
    )
    scores = await database.list_anomaly_scores(instance_id=inst_id)
    assert len(scores) >= 2


@pytest.mark.usefixtures("_check_db_available")
async def test_get_latest_anomaly_score(db: tuple[TimescaleDBDatabase, int]) -> None:
    """En son anomali skorunu döndürmeli."""
    database, inst_id = db
    t1 = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    t2 = datetime(2026, 1, 1, 0, 1, tzinfo=UTC)
    await database.insert_anomaly_score(
        AnomalyScore(instance_id=inst_id, timestamp=t1, score=0.5),
    )
    await database.insert_anomaly_score(
        AnomalyScore(instance_id=inst_id, timestamp=t2, score=-0.3, is_anomaly=True),
    )
    latest = await database.get_latest_anomaly_score(instance_id=inst_id)
    assert latest is not None
    assert latest.score == -0.3
    assert latest.is_anomaly is True
