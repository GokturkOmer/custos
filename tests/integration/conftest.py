"""Integration test'ler için ortak fixture'lar.

- `_check_db_available`: TimescaleDB ayakta değilse testleri atlar
- `db`: DB bağlantısı + maintenance/threshold/tag prefix'li test row'larını
  FK bağımlılık sırasında temizler
- ``_bypass_auth``: V11-101 sonrası — custos main app kullanan integration
  testleri için otomatik developer-session override

Not: Mevcut (F8a öncesi) test dosyaları kendi `db` fixture'larını tanımlıyor
ve pytest local-scope öncelikli olduğundan onlar etkilenmez. Yeni F8a
testleri (maintenance_*) fixture tanımlamayıp conftest'tekini kullanır.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import asyncpg
import pytest

from custos.__main__ import app
from custos.analytics.dashboard.auth_dependencies import (
    require_developer,
    require_operator,
)
from custos.shared.config import Settings
from custos.shared.database import Session, TimescaleDBDatabase

_FAR_FUTURE = datetime(2099, 1, 1, tzinfo=UTC)
_TEST_DEV_SESSION = Session(
    id=1,
    user_id=1,
    username="test_dev_integration",
    role="developer",
    enabled=True,
    must_change_password=False,
    expires_at=_FAR_FUTURE,
)


def _fake_dev_session() -> Session:
    """V11-101 dependency override için sahte developer session."""
    return _TEST_DEV_SESSION


@pytest.fixture(autouse=True)
def _bypass_auth_for_integration_tests() -> object:
    """Custos main app kullanan integration testleri için auth bypass.

    Test kendi minimal FastAPI app'ini oluşturuyorsa (örn. test_parquet_archiver
    `_build_test_app`), o app'e ayrıca override eklenmesi gerekir.
    """
    app.dependency_overrides[require_operator] = _fake_dev_session
    app.dependency_overrides[require_developer] = _fake_dev_session
    try:
        yield
    finally:
        app.dependency_overrides.pop(require_operator, None)
        app.dependency_overrides.pop(require_developer, None)


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
