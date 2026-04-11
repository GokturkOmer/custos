"""Audit Log entegrasyon testleri.

TimescaleDB'nin ayakta olmasını gerektirir (docker compose up -d).
"""

from __future__ import annotations

import asyncio

import pytest

from custos.shared.config import Settings
from custos.shared.database import AuditLogEntry, TimescaleDBDatabase


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
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM audit_log WHERE entity_id LIKE 'TEST_%'")
    yield database  # type: ignore[misc]
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM audit_log WHERE entity_id LIKE 'TEST_%'")
    await database.close()


@pytest.mark.usefixtures("_check_db_available")
async def test_insert_audit_log(db: TimescaleDBDatabase) -> None:
    """Audit log kaydı oluşturup geri okunabiliyor mu?"""
    entry = AuditLogEntry(
        category="alarm",
        action="triggered",
        entity_type="threshold",
        entity_id="TEST_1",
        detail="Test alarm tetiklendi",
    )
    created = await db.insert_audit_log(entry)

    assert created.id is not None
    assert created.category == "alarm"
    assert created.timestamp is not None


@pytest.mark.usefixtures("_check_db_available")
async def test_list_audit_log_filter_by_category(db: TimescaleDBDatabase) -> None:
    """Kategori filtresi çalışıyor mu?"""
    await db.insert_audit_log(
        AuditLogEntry(
            category="alarm",
            action="triggered",
            entity_id="TEST_F1",
        ),
    )
    await db.insert_audit_log(
        AuditLogEntry(
            category="tag",
            action="created",
            entity_id="TEST_F2",
        ),
    )

    alarm_logs = await db.list_audit_log(category="alarm")
    assert all(e.category == "alarm" for e in alarm_logs)

    tag_logs = await db.list_audit_log(category="tag")
    assert all(e.category == "tag" for e in tag_logs)


@pytest.mark.usefixtures("_check_db_available")
async def test_count_audit_log(db: TimescaleDBDatabase) -> None:
    """Audit log count çalışıyor mu?"""
    await db.insert_audit_log(
        AuditLogEntry(
            category="system",
            action="test",
            entity_id="TEST_C1",
        ),
    )

    total = await db.count_audit_log()
    assert total >= 1

    system_count = await db.count_audit_log(category="system")
    assert system_count >= 1
