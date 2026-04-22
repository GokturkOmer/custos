"""Bulk tag import entegrasyon testleri — process_bulk_import + DB.

TimescaleDB'nin ayakta olmasını gerektirir (docker compose up -d).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from custos.analytics.dashboard.bulk_import import (
    DuplicateMode,
    process_bulk_import,
)
from custos.shared.config import Settings
from custos.shared.database import TagRecord, TimescaleDBDatabase


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
async def db() -> AsyncIterator[TimescaleDBDatabase]:
    """Test için DB bağlantısı oluşturur; TEST_BULK_ prefix'li satırları temizler."""
    s = Settings()
    database = TimescaleDBDatabase(s)
    await database.connect()
    pool = database._get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM tags WHERE tag_id LIKE 'TEST_BULK_%'")
    yield database
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM tags WHERE tag_id LIKE 'TEST_BULK_%'")
    await database.close()


def _raw_row(suffix: str, **overrides: object) -> dict[str, object]:
    """Test için ham satır sözlüğü (parse_csv çıktısı formatında)."""
    base: dict[str, object] = {
        "tag_id": f"TEST_BULK_{suffix}",
        "name": f"Test Bulk {suffix}",
        "modbus_host": "10.0.0.99",
        "register_address": 40001,
        "register_type": "uint16",
        "polling_interval_ms": 10000,
        "unit": "°C",
        "gain": 1.0,
        "offset": 0.0,
    }
    base.update(overrides)
    return base


@pytest.mark.usefixtures("_check_db_available")
async def test_commit_inserts_new_tags(db: TimescaleDBDatabase) -> None:
    """Temiz DB → 3 yeni tag eklenir, inserted=3."""
    rows = [_raw_row(f"{i:03d}") for i in range(3)]
    result = await process_bulk_import(db, rows, DuplicateMode.REJECT)

    assert result.ok is True
    assert result.inserted == 3
    assert result.updated == 0
    assert result.skipped == 0

    # DB'de gerçekten var mı?
    for i in range(3):
        tag = await db.get_tag(f"TEST_BULK_{i:03d}")
        assert tag is not None
        assert tag.modbus_host == "10.0.0.99"


@pytest.mark.usefixtures("_check_db_available")
async def test_commit_reject_mode_existing_tag(db: TimescaleDBDatabase) -> None:
    """Mevcut tag_id + reject → hiçbiri yazılmaz, ok=False."""
    # Önce bir tag ekle
    existing = TagRecord(
        tag_id="TEST_BULK_EXIST",
        name="Existing",
        modbus_host="1.1.1.1",
        register_address=100,
    )
    await db.insert_tag(existing)

    rows = [
        _raw_row("EXIST"),  # DB'de zaten var
        _raw_row("NEW01"),  # yeni
    ]
    result = await process_bulk_import(db, rows, DuplicateMode.REJECT)

    assert result.ok is False
    assert result.inserted == 0
    assert len(result.errors) >= 1
    assert "zaten var" in result.errors[0].message

    # Yeni olan da eklenmemiş olmalı (atomik davranış)
    new_tag = await db.get_tag("TEST_BULK_NEW01")
    assert new_tag is None


@pytest.mark.usefixtures("_check_db_available")
async def test_commit_update_mode_overwrites_existing(db: TimescaleDBDatabase) -> None:
    """Update modda mevcut kayıt güncellenir, yeniler eklenir."""
    original = TagRecord(
        tag_id="TEST_BULK_UPDT",
        name="Eski İsim",
        modbus_host="0.0.0.0",
        register_address=100,
    )
    await db.insert_tag(original)

    rows = [
        _raw_row("UPDT", name="Yeni İsim", modbus_host="9.9.9.9"),
        _raw_row("NEWA"),
    ]
    result = await process_bulk_import(db, rows, DuplicateMode.UPDATE)

    assert result.ok is True
    assert result.inserted == 1
    assert result.updated == 1
    assert result.skipped == 0

    updated_tag = await db.get_tag("TEST_BULK_UPDT")
    assert updated_tag is not None
    assert updated_tag.name == "Yeni İsim"
    assert updated_tag.modbus_host == "9.9.9.9"


@pytest.mark.usefixtures("_check_db_available")
async def test_commit_insert_mode_skips_existing(db: TimescaleDBDatabase) -> None:
    """Insert modda mevcut tag'ler skip edilir, yalnız yeniler eklenir."""
    original = TagRecord(
        tag_id="TEST_BULK_SKIP",
        name="Orijinal",
        modbus_host="0.0.0.0",
        register_address=100,
    )
    await db.insert_tag(original)

    rows = [
        _raw_row("SKIP", name="Bu isim UPDATE edilmemeli"),
        _raw_row("FRESH"),
    ]
    result = await process_bulk_import(db, rows, DuplicateMode.INSERT)

    assert result.ok is True
    assert result.inserted == 1
    assert result.updated == 0
    assert result.skipped == 1

    # Orijinal isim korunmalı
    unchanged = await db.get_tag("TEST_BULK_SKIP")
    assert unchanged is not None
    assert unchanged.name == "Orijinal"

    fresh = await db.get_tag("TEST_BULK_FRESH")
    assert fresh is not None


@pytest.mark.usefixtures("_check_db_available")
async def test_commit_validation_error_blocks_all(db: TimescaleDBDatabase) -> None:
    """Tek invalid satır → hiçbiri yazılmaz (atomik)."""
    rows = [
        _raw_row("VAL01"),
        _raw_row("VAL02", register_type="invalid_type"),
        _raw_row("VAL03"),
    ]
    result = await process_bulk_import(db, rows, DuplicateMode.REJECT)

    assert result.ok is False
    assert result.inserted == 0
    assert any(e.field == "register_type" for e in result.errors)

    # Diğerleri de yazılmamış olmalı
    for suffix in ("VAL01", "VAL03"):
        assert await db.get_tag(f"TEST_BULK_{suffix}") is None


@pytest.mark.usefixtures("_check_db_available")
async def test_commit_duplicate_within_file(db: TimescaleDBDatabase) -> None:
    """Aynı dosyada tekrar eden tag_id → hata, hiçbiri yazılmaz."""
    rows = [_raw_row("DUPE"), _raw_row("DUPE")]
    result = await process_bulk_import(db, rows, DuplicateMode.REJECT)

    assert result.ok is False
    assert await db.get_tag("TEST_BULK_DUPE") is None


@pytest.mark.usefixtures("_check_db_available")
async def test_commit_converts_modbus_address(db: TimescaleDBDatabase) -> None:
    """Konvansiyonel adres 40001+ → 0-based DB'ye yazılmalı."""
    rows = [_raw_row("ADDR", register_address=40042)]
    result = await process_bulk_import(db, rows, DuplicateMode.REJECT)

    assert result.ok is True
    tag = await db.get_tag("TEST_BULK_ADDR")
    assert tag is not None
    assert tag.register_address == 41  # 40042 - 40001


@pytest.mark.usefixtures("_check_db_available")
async def test_commit_200_tags_performance(db: TimescaleDBDatabase) -> None:
    """200 tag insert edilebilmeli — pilot iş yüküne yakın smoke test."""
    rows = [_raw_row(f"{i:03d}") for i in range(200)]
    result = await process_bulk_import(db, rows, DuplicateMode.REJECT)

    assert result.ok is True
    assert result.inserted == 200
