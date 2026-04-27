"""service_heartbeats CRUD entegrasyon testleri (V11-105/K13)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

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
    """Test için DB bağlantısı oluşturur ve service_heartbeats tablosunu temizler.

    Migration 029 çalıştırılmamış (tablo yok) ise testleri atla — lokal dev
    DB'leri ``alembic upgrade head`` koşulmadan kalmış olabilir.
    """
    s = Settings()
    database = TimescaleDBDatabase(s)
    await database.connect()
    pool = database._get_pool()
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM service_heartbeats WHERE service_name LIKE 'TEST_%'"
            )
    except Exception as exc:
        await database.close()
        pytest.skip(
            f"service_heartbeats tablosu yok — 'alembic upgrade head' çalıştır ({exc})"
        )
    yield database  # type: ignore[misc]
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM service_heartbeats WHERE service_name LIKE 'TEST_%'"
        )
    await database.close()


@pytest.mark.usefixtures("_check_db_available")
async def test_write_heartbeat_inserts_new_row(db: TimescaleDBDatabase) -> None:
    """İlk yazımda satır oluşur, last_heartbeat_at NOW() civarında olur."""
    await db.write_service_heartbeat("TEST_HB_NEW", status="active")

    rows = await db.list_service_heartbeats()
    by_name = {r.service_name: r for r in rows}
    assert "TEST_HB_NEW" in by_name
    hb = by_name["TEST_HB_NEW"]
    assert hb.status == "active"
    age = (datetime.now(UTC) - hb.last_heartbeat_at).total_seconds()
    assert 0 <= age < 5  # 5 sn'den taze


@pytest.mark.usefixtures("_check_db_available")
async def test_write_heartbeat_upserts_existing(db: TimescaleDBDatabase) -> None:
    """İkinci yazımda last_heartbeat_at güncellenir, satır artmaz."""
    await db.write_service_heartbeat("TEST_HB_UPSERT")
    await asyncio.sleep(1.1)  # Net bir fark yarat (timestamp resolution)
    await db.write_service_heartbeat("TEST_HB_UPSERT", status="busy")

    rows = await db.list_service_heartbeats()
    matching = [r for r in rows if r.service_name == "TEST_HB_UPSERT"]
    assert len(matching) == 1
    assert matching[0].status == "busy"


@pytest.mark.usefixtures("_check_db_available")
async def test_write_heartbeat_metadata_roundtrip(db: TimescaleDBDatabase) -> None:
    """metadata JSONB doğru serialize/deserialize olmalı."""
    await db.write_service_heartbeat(
        "TEST_HB_META",
        metadata={"version": "0.1.0-dev", "tag_count": 200},
    )

    rows = await db.list_service_heartbeats()
    by_name = {r.service_name: r for r in rows}
    hb = by_name["TEST_HB_META"]
    assert hb.metadata is not None
    assert hb.metadata["version"] == "0.1.0-dev"
    assert hb.metadata["tag_count"] == 200


@pytest.mark.usefixtures("_check_db_available")
async def test_write_heartbeat_updates_age_after_sleep(
    db: TimescaleDBDatabase,
) -> None:
    """Tekrar yazım sonrası yaş 1 sn altına düşer (timestamp ileri)."""
    await db.write_service_heartbeat("TEST_HB_AGE")
    # Eski timestamp'i manuel geri al
    pool = db._get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE service_heartbeats
            SET last_heartbeat_at = $1
            WHERE service_name = $2
            """,
            datetime.now(UTC) - timedelta(seconds=200),
            "TEST_HB_AGE",
        )

    # Şimdi yeniden yaz — yaş sıfırlanmalı
    await db.write_service_heartbeat("TEST_HB_AGE")
    rows = await db.list_service_heartbeats()
    hb = next(r for r in rows if r.service_name == "TEST_HB_AGE")
    age = (datetime.now(UTC) - hb.last_heartbeat_at).total_seconds()
    assert age < 5
