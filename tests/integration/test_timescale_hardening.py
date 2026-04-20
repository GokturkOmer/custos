"""TimescaleDB hardening (F11 Paket A) integration testleri.

Migration 024 sonrası tag_readings ve features hypertable'larında:
- Chunk interval = 1 gün
- Compression enabled, segmentby='tag_id', orderby='timestamp DESC'
- Compression policy = 7 gün
- Retention policy = 365 gün

Tüm kontroller `timescaledb_information` katalog görünümlerinden okur;
veri yazılmaz, sadece DB metadata sorgulanır.
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any, cast

import pytest

from custos.shared.database import TimescaleDBDatabase

EXPECTED_CHUNK_INTERVAL = timedelta(days=1)


def _parse_config(raw: Any) -> dict[str, Any]:
    """jobs.config bazen str (asyncpg jsonb codec yok) bazen dict gelebilir."""
    if isinstance(raw, dict):
        return cast(dict[str, Any], raw)
    if isinstance(raw, str):
        return cast(dict[str, Any], json.loads(raw))
    return {}


@pytest.mark.usefixtures("_check_db_available")
async def test_compression_enabled_on_tag_readings(
    db: TimescaleDBDatabase,
) -> None:
    """tag_readings hypertable'ında compression açık ve doğru parametrelerle.

    TS 2.x compression_settings per-column row döndürür; segmentby ve orderby
    kolonları index üzerinden tanımlanır.
    """
    pool = db._get_pool()
    async with pool.acquire() as conn:
        enabled = await conn.fetchval(
            "SELECT compression_enabled "
            "FROM timescaledb_information.hypertables "
            "WHERE hypertable_name = 'tag_readings'"
        )
        assert enabled is True, "tag_readings compression açık değil"

        rows = await conn.fetch(
            "SELECT attname, segmentby_column_index, "
            "       orderby_column_index, orderby_asc "
            "FROM timescaledb_information.compression_settings "
            "WHERE hypertable_name = 'tag_readings'"
        )
        segmentby = [
            r["attname"]
            for r in rows
            if r["segmentby_column_index"] is not None
        ]
        orderby = [
            (r["attname"], r["orderby_asc"])
            for r in rows
            if r["orderby_column_index"] is not None
        ]
        assert segmentby == ["tag_id"], (
            f"segmentby beklenen ['tag_id'], alınan: {segmentby!r}"
        )
        # orderby_asc=False → DESC
        assert orderby == [("timestamp", False)], (
            f"orderby beklenen [('timestamp', False)], alınan: {orderby!r}"
        )


@pytest.mark.usefixtures("_check_db_available")
async def test_compression_policy_7_days(
    db: TimescaleDBDatabase,
) -> None:
    """tag_readings için 7 günlük compression policy kayıtlı."""
    pool = db._get_pool()
    async with pool.acquire() as conn:
        raw = await conn.fetchval(
            "SELECT config "
            "FROM timescaledb_information.jobs "
            "WHERE proc_name = 'policy_compression' "
            "  AND hypertable_name = 'tag_readings'"
        )
        assert raw is not None, "compression policy kaydı bulunamadı"
        config = _parse_config(raw)
        assert config.get("compress_after") == "7 days", (
            f"compress_after beklenen '7 days', alınan: "
            f"{config.get('compress_after')!r}"
        )


@pytest.mark.usefixtures("_check_db_available")
async def test_retention_policy_365_days_tag_readings(
    db: TimescaleDBDatabase,
) -> None:
    """tag_readings için 365 günlük retention policy kayıtlı."""
    pool = db._get_pool()
    async with pool.acquire() as conn:
        raw = await conn.fetchval(
            "SELECT config "
            "FROM timescaledb_information.jobs "
            "WHERE proc_name = 'policy_retention' "
            "  AND hypertable_name = 'tag_readings'"
        )
        assert raw is not None, "retention policy kaydı bulunamadı"
        config = _parse_config(raw)
        assert config.get("drop_after") == "365 days", (
            f"drop_after beklenen '365 days', alınan: "
            f"{config.get('drop_after')!r}"
        )


@pytest.mark.usefixtures("_check_db_available")
async def test_chunk_interval_1_day_tag_readings(
    db: TimescaleDBDatabase,
) -> None:
    """tag_readings hypertable chunk interval'ı 1 gün."""
    pool = db._get_pool()
    async with pool.acquire() as conn:
        interval = await conn.fetchval(
            "SELECT time_interval "
            "FROM timescaledb_information.dimensions "
            "WHERE hypertable_name = 'tag_readings'"
        )
        assert interval == EXPECTED_CHUNK_INTERVAL, (
            f"chunk interval beklenen 1 gün, alınan: {interval}"
        )


@pytest.mark.usefixtures("_check_db_available")
async def test_features_hypertable_also_hardened(
    db: TimescaleDBDatabase,
) -> None:
    """features hypertable'ı da aynı 4 ayara sahip (chunk/comp/policy/ret)."""
    pool = db._get_pool()
    async with pool.acquire() as conn:
        # 1) Chunk interval = 1 gün
        interval = await conn.fetchval(
            "SELECT time_interval "
            "FROM timescaledb_information.dimensions "
            "WHERE hypertable_name = 'features'"
        )
        assert interval == EXPECTED_CHUNK_INTERVAL, (
            f"features chunk interval 1 gün değil: {interval}"
        )

        # 2) Compression enabled + segmentby/orderby
        enabled = await conn.fetchval(
            "SELECT compression_enabled "
            "FROM timescaledb_information.hypertables "
            "WHERE hypertable_name = 'features'"
        )
        assert enabled is True, "features compression açık değil"

        rows = await conn.fetch(
            "SELECT attname, segmentby_column_index, "
            "       orderby_column_index, orderby_asc "
            "FROM timescaledb_information.compression_settings "
            "WHERE hypertable_name = 'features'"
        )
        segmentby = [
            r["attname"]
            for r in rows
            if r["segmentby_column_index"] is not None
        ]
        orderby = [
            (r["attname"], r["orderby_asc"])
            for r in rows
            if r["orderby_column_index"] is not None
        ]
        assert segmentby == ["tag_id"]
        assert orderby == [("timestamp", False)]

        # 3) Compression policy = 7 gün
        comp_raw = await conn.fetchval(
            "SELECT config "
            "FROM timescaledb_information.jobs "
            "WHERE proc_name = 'policy_compression' "
            "  AND hypertable_name = 'features'"
        )
        assert comp_raw is not None, "features compression policy yok"
        assert _parse_config(comp_raw).get("compress_after") == "7 days"

        # 4) Retention policy = 365 gün
        ret_raw = await conn.fetchval(
            "SELECT config "
            "FROM timescaledb_information.jobs "
            "WHERE proc_name = 'policy_retention' "
            "  AND hypertable_name = 'features'"
        )
        assert ret_raw is not None, "features retention policy yok"
        assert _parse_config(ret_raw).get("drop_after") == "365 days"
