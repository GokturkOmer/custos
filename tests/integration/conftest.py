"""Integration test'ler için ortak fixture'lar.

- `_check_db_available`: TimescaleDB ayakta değilse testleri atlar
- `db`: DB bağlantısı + maintenance/threshold/tag prefix'li test row'larını
  FK bağımlılık sırasında temizler

Not: Mevcut (F8a öncesi) test dosyaları kendi `db` fixture'larını tanımlıyor
ve pytest local-scope öncelikli olduğundan onlar etkilenmez. Yeni F8a
testleri (maintenance_*) fixture tanımlamayıp conftest'tekini kullanır.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import asyncpg
import pytest

from custos.shared.config import Settings
from custos.shared.database import TimescaleDBDatabase


async def _cleanup_test_rows(pool: asyncpg.Pool) -> None:
    """Test prefix'li tüm satırları FK bağımlılık sırasında siler.

    Silme sırası: bağımlı olanlardan bağımsız olanlara. Örn. alarm mapping
    threshold ve checklist'e bağlıdır, önce o silinir.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM alarm_checklist_mappings "
            "WHERE threshold_id IN ("
            " SELECT id FROM thresholds WHERE tag_id LIKE 'TEST_%'"
            ") OR checklist_id IN ("
            " SELECT id FROM maintenance_checklists WHERE slug LIKE 'test-%'"
            ")",
        )
        await conn.execute(
            "DELETE FROM maintenance_tasks WHERE checklist_id IN ("
            " SELECT id FROM maintenance_checklists WHERE slug LIKE 'test-%'"
            ")",
        )
        await conn.execute(
            "DELETE FROM maintenance_schedules WHERE checklist_id IN ("
            " SELECT id FROM maintenance_checklists WHERE slug LIKE 'test-%'"
            ")",
        )
        await conn.execute(
            "DELETE FROM alarm_events WHERE threshold_id IN ("
            " SELECT id FROM thresholds WHERE tag_id LIKE 'TEST_%'"
            ")",
        )
        await conn.execute(
            "DELETE FROM thresholds WHERE tag_id LIKE 'TEST_%'",
        )
        await conn.execute(
            "DELETE FROM maintenance_checklists WHERE slug LIKE 'test-%'",
        )
        await conn.execute(
            "DELETE FROM tags WHERE tag_id LIKE 'TEST_%'",
        )


@pytest.fixture
def _check_db_available() -> None:
    """TimescaleDB erişilebilir değilse testi atla."""

    async def _probe() -> bool:
        s = Settings()
        database = TimescaleDBDatabase(s)
        try:
            await database.connect()
            result = await database.health_check()
            await database.close()
        except Exception:
            return False
        else:
            return result

    if not asyncio.run(_probe()):
        pytest.skip("TimescaleDB ayakta değil — 'docker compose up -d' çalıştır")


@pytest.fixture
async def db() -> AsyncIterator[TimescaleDBDatabase]:
    """Maintenance + threshold + tag test satırlarını temizleyen DB fixture."""
    s = Settings()
    database = TimescaleDBDatabase(s)
    await database.connect()
    pool = database._get_pool()
    await _cleanup_test_rows(pool)
    try:
        yield database
    finally:
        await _cleanup_test_rows(pool)
        await database.close()
