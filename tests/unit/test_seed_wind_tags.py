"""scripts/seed_wind_tags.py — birim testleri (Faz 1.3).

Kapsam:
- load_tag_map: header validasyonu + satir parse.
- build_tag_record: CSV row → TagRecord eslemesi (default'lar dahil).
- check_postgres_db_guard: POSTGRES_DB env var kontrol.
- find_template_by_slug + ensure_asset_instance: get-or-create idempotency.
- seed_tags: tag insert + duplicate skip.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# scripts/ klasoru bir paket olmadigi icin import path'ine elle ekliyoruz.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from seed_wind_tags import (  # noqa: E402 — sys.path manipulation sonrasi import
    DEFAULT_MODBUS_HOST,
    DEFAULT_MODBUS_PORT,
    DEFAULT_POLLING_INTERVAL_MS,
    EXPECTED_POSTGRES_DB,
    build_tag_record,
    check_postgres_db_guard,
    ensure_asset_instance,
    find_template_by_slug,
    load_tag_map,
    seed_tags,
)

from custos.shared.database import (  # noqa: E402 — sys.path manipulation sonrasi import
    AssetInstance,
    AssetTemplate,
    TagRecord,
)

# --- load_tag_map ---


def _write_csv(tmp_path: Path, lines: list[str]) -> Path:
    """Test fixture CSV dosyasi olusturur."""
    path = tmp_path / "tag_map.csv"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_load_tag_map_parses_valid_csv(tmp_path: Path) -> None:
    """Header + 2 satirlik CSV → 2 row donmeli, dict-form korunmali."""
    csv_path = _write_csv(
        tmp_path,
        [
            "custos_tag_name,register_address,register_type,gain,offset,unit,description",
            "wind_t_test_a,1000,uint16,0.1,0.0,C,Test A",
            "wind_t_test_b,1001,int16,0.01,0.0,deg,Test B",
        ],
    )
    rows = load_tag_map(csv_path)
    assert len(rows) == 2
    assert rows[0]["custos_tag_name"] == "wind_t_test_a"
    assert rows[1]["register_type"] == "int16"


def test_load_tag_map_raises_on_missing_required_column(tmp_path: Path) -> None:
    """Zorunlu kolon eksikse ValueError firlatmali (kolon listesini icermeli)."""
    csv_path = _write_csv(
        tmp_path,
        [
            # register_type kolonu eksik
            "custos_tag_name,register_address,gain",
            "wind_t_test_a,1000,0.1",
        ],
    )
    with pytest.raises(ValueError, match="register_type"):
        load_tag_map(csv_path)


def test_load_tag_map_raises_on_empty_file(tmp_path: Path) -> None:
    """Bos dosya = header yok = ValueError."""
    csv_path = tmp_path / "empty.csv"
    csv_path.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="header yok|bos"):
        load_tag_map(csv_path)


# --- build_tag_record ---


def test_build_tag_record_basic_mapping() -> None:
    """CSV row tum alanlari TagRecord'a esler; description name'e gider."""
    row = {
        "custos_tag_name": "wind_t_sample",
        "description": "Test sicaklik",
        "unit": "C",
        "register_address": "1042",
        "register_type": "int16",
        "gain": "0.01",
        "offset": "5.0",
    }
    tag = build_tag_record(
        row=row,
        modbus_host="10.0.0.5",
        modbus_port=5021,
        unit_id=3,
        polling_interval_ms=30_000,
    )
    assert isinstance(tag, TagRecord)
    assert tag.tag_id == "wind_t_sample"
    assert tag.name == "Test sicaklik"
    assert tag.modbus_host == "10.0.0.5"
    assert tag.modbus_port == 5021
    assert tag.unit_id == 3
    assert tag.register_address == 1042
    assert tag.register_type == "int16"
    assert tag.gain == 0.01
    assert tag.offset == 5.0
    assert tag.unit == "C"
    assert tag.polling_interval_ms == 30_000
    assert tag.byte_order == "big"
    assert tag.status == "active"


def test_build_tag_record_uses_defaults_for_missing_optional_fields() -> None:
    """``gain``/``offset``/``unit``/``description`` bos/missing → default."""
    row = {
        "custos_tag_name": "wind_t_minimal",
        "register_address": "2000",
        "register_type": "uint16",
        # gain, offset, unit, description hep yok
    }
    tag = build_tag_record(
        row=row,
        modbus_host=DEFAULT_MODBUS_HOST,
        modbus_port=DEFAULT_MODBUS_PORT,
        unit_id=1,
        polling_interval_ms=DEFAULT_POLLING_INTERVAL_MS,
    )
    assert tag.gain == 1.0
    assert tag.offset == 0.0
    assert tag.unit == ""
    # description bos ise name tag_id'ye fallback
    assert tag.name == "wind_t_minimal"


def test_build_tag_record_strips_whitespace() -> None:
    """CSV'de bosluklar olabilir — strip edilmeli (tag_id, register_type, unit)."""
    row = {
        "custos_tag_name": "  wind_t_padded  ",
        "description": "  Test  ",
        "unit": "  C  ",
        "register_address": "1000",
        "register_type": "  int16  ",
        "gain": " 0.01 ",
        "offset": " 0.0 ",
    }
    tag = build_tag_record(
        row=row,
        modbus_host="127.0.0.1",
        modbus_port=5021,
        unit_id=1,
        polling_interval_ms=60_000,
    )
    assert tag.tag_id == "wind_t_padded"
    assert tag.register_type == "int16"
    assert tag.unit == "C"
    assert tag.name == "Test"


# --- check_postgres_db_guard ---


def test_db_guard_passes_when_env_is_custos_wind(monkeypatch: pytest.MonkeyPatch) -> None:
    """``POSTGRES_DB=custos_wind`` → guard None doner (yesil)."""
    monkeypatch.setenv("POSTGRES_DB", EXPECTED_POSTGRES_DB)
    assert check_postgres_db_guard() is None


def test_db_guard_fails_for_avm_production_db(monkeypatch: pytest.MonkeyPatch) -> None:
    """``POSTGRES_DB=custos`` → guard hata mesaji doner (AVM koruma)."""
    monkeypatch.setenv("POSTGRES_DB", "custos")
    err = check_postgres_db_guard()
    assert err is not None
    assert "custos_wind" in err
    assert ".env.wind" in err


def test_db_guard_fails_when_env_var_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """``POSTGRES_DB`` env yoksa guard hata doner."""
    monkeypatch.delenv("POSTGRES_DB", raising=False)
    err = check_postgres_db_guard()
    assert err is not None


# --- find_template_by_slug ---


@pytest.mark.asyncio
async def test_find_template_by_slug_returns_match() -> None:
    """Slug eslesirse template doner."""
    db = MagicMock()
    db.list_asset_templates = AsyncMock(
        return_value=[
            AssetTemplate(slug="ahu", name="AHU", id=1),
            AssetTemplate(slug="wind_turbine_v1", name="Wind", id=42),
        ],
    )
    result = await find_template_by_slug(db, "wind_turbine_v1")
    assert result is not None
    assert result.id == 42


@pytest.mark.asyncio
async def test_find_template_by_slug_returns_none_when_missing() -> None:
    """Slug bulunamazsa None doner."""
    db = MagicMock()
    db.list_asset_templates = AsyncMock(
        return_value=[AssetTemplate(slug="ahu", name="AHU", id=1)],
    )
    result = await find_template_by_slug(db, "missing_slug")
    assert result is None


# --- ensure_asset_instance ---


@pytest.mark.asyncio
async def test_ensure_asset_instance_creates_when_not_present() -> None:
    """Mevcut yoksa insert_asset_instance cagrilir, created=True."""
    template = AssetTemplate(slug="wind_turbine_v1", name="Wind", id=42)
    db = MagicMock()
    db.list_asset_instances = AsyncMock(return_value=[])  # bos liste
    inserted = AssetInstance(template_id=42, name="wind_turbine_01", id=99)
    db.insert_asset_instance = AsyncMock(return_value=inserted)

    instance, created = await ensure_asset_instance(
        db,
        name="wind_turbine_01",
        template=template,
        description="Test",
    )
    assert created is True
    assert instance.id == 99
    db.insert_asset_instance.assert_awaited_once()


@pytest.mark.asyncio
async def test_ensure_asset_instance_returns_existing_when_name_matches() -> None:
    """Ayni name varsa insert cagrilmaz, mevcut doner."""
    template = AssetTemplate(slug="wind_turbine_v1", name="Wind", id=42)
    existing = AssetInstance(template_id=42, name="wind_turbine_01", id=77)
    db = MagicMock()
    db.list_asset_instances = AsyncMock(return_value=[existing])
    db.insert_asset_instance = AsyncMock()

    instance, created = await ensure_asset_instance(
        db,
        name="wind_turbine_01",
        template=template,
        description="Test",
    )
    assert created is False
    assert instance.id == 77
    db.insert_asset_instance.assert_not_called()


# --- seed_tags ---


def _row(tag_id: str, addr: int = 1000) -> dict[str, str]:
    """Kucuk fixture: minimum CSV row."""
    return {
        "custos_tag_name": tag_id,
        "register_address": str(addr),
        "register_type": "int16",
        "gain": "0.01",
        "offset": "0.0",
        "description": f"Tag {tag_id}",
        "unit": "C",
    }


@pytest.mark.asyncio
async def test_seed_tags_inserts_when_absent() -> None:
    """get_tag None doner → insert cagrilir, added artar."""
    db = MagicMock()
    db.get_tag = AsyncMock(return_value=None)
    db.insert_tag = AsyncMock(side_effect=lambda t: t)

    added, skipped = await seed_tags(
        db,
        [_row("wind_t_a", 1000), _row("wind_t_b", 1001)],
        modbus_host="127.0.0.1",
        modbus_port=5021,
        unit_id=1,
        polling_interval_ms=60_000,
    )
    assert added == 2
    assert skipped == 0
    assert db.insert_tag.await_count == 2


@pytest.mark.asyncio
async def test_seed_tags_skips_existing() -> None:
    """get_tag mevcut doner → insert cagrilmaz, skipped artar."""
    existing = TagRecord(
        tag_id="wind_t_a", name="x", modbus_host="127.0.0.1", register_address=1000,
    )
    db = MagicMock()
    db.get_tag = AsyncMock(return_value=existing)
    db.insert_tag = AsyncMock()

    added, skipped = await seed_tags(
        db,
        [_row("wind_t_a", 1000)],
        modbus_host="127.0.0.1",
        modbus_port=5021,
        unit_id=1,
        polling_interval_ms=60_000,
    )
    assert added == 0
    assert skipped == 1
    db.insert_tag.assert_not_called()


@pytest.mark.asyncio
async def test_seed_tags_is_idempotent_on_repeated_run() -> None:
    """Iki kez calistirilirsa ikinci kez 0 insert (mock get_tag 2. carkta mevcut doner)."""
    inserted: dict[str, TagRecord] = {}

    async def _get_tag(tag_id: str) -> TagRecord | None:
        return inserted.get(tag_id)

    async def _insert(tag: TagRecord) -> TagRecord:
        inserted[tag.tag_id] = tag
        return tag

    db = MagicMock()
    db.get_tag = AsyncMock(side_effect=_get_tag)
    db.insert_tag = AsyncMock(side_effect=_insert)

    rows = [_row("wind_t_a", 1000), _row("wind_t_b", 1001)]

    added1, skipped1 = await seed_tags(
        db, rows,
        modbus_host="127.0.0.1", modbus_port=5021, unit_id=1,
        polling_interval_ms=60_000,
    )
    added2, skipped2 = await seed_tags(
        db, rows,
        modbus_host="127.0.0.1", modbus_port=5021, unit_id=1,
        polling_interval_ms=60_000,
    )

    assert (added1, skipped1) == (2, 0)
    assert (added2, skipped2) == (0, 2)


@pytest.mark.asyncio
async def test_seed_tags_skips_rows_with_empty_tag_id() -> None:
    """``custos_tag_name`` bossa satir atlanir (sayilmaz)."""
    db = MagicMock()
    db.get_tag = AsyncMock(return_value=None)
    db.insert_tag = AsyncMock(side_effect=lambda t: t)

    rows = [
        _row("wind_t_a", 1000),
        {"custos_tag_name": "  ", "register_address": "1001", "register_type": "uint16"},
        _row("wind_t_b", 1002),
    ]
    added, skipped = await seed_tags(
        db, rows,
        modbus_host="127.0.0.1", modbus_port=5021, unit_id=1,
        polling_interval_ms=60_000,
    )
    # Bos satir hicbir kategoriye sayilmaz (added=2, skipped=0).
    assert added == 2
    assert skipped == 0
