"""Continuous aggregates (F11 Paket B) integration testleri.

Migration 025 sonrası:

- ``tag_readings_1min`` CA mevcut, refresh policy var, 3 yıl retention.
- ``tag_readings_1hour`` CA mevcut, refresh policy var, retention YOK (sınırsız).
- 1hour ``tag_readings_1min``'den türetilmiş (hierarchical CA).

Refresh policy ve retention kayıtları ``timescaledb_information.jobs`` üzerinden,
CA tanımları ``timescaledb_information.continuous_aggregates`` üzerinden okunur.
AVG doğrulama için ham veri insert edilir ve manuel ``refresh_continuous_aggregate``
ile bucket doldurulur.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest

from custos.shared.database import (
    TagReading,
    TagRecord,
    TimescaleDBDatabase,
)


def _parse_config(raw: Any) -> dict[str, Any]:
    """jobs.config bazen str (asyncpg jsonb codec yok) bazen dict gelebilir."""
    if isinstance(raw, dict):
        return cast(dict[str, Any], raw)
    if isinstance(raw, str):
        return cast(dict[str, Any], json.loads(raw))
    return {}


def _interval_matches(value: str | None, candidates: list[str]) -> bool:
    """TimescaleDB interval field'ı '3 hours' veya '03:00:00' şeklinde gelebilir."""
    return value in candidates


@pytest.mark.usefixtures("_check_db_available")
async def test_1min_aggregate_exists_and_has_policy(
    db: TimescaleDBDatabase,
) -> None:
    """tag_readings_1min CA kayıtlı ve refresh policy var."""
    pool = db._get_pool()
    async with pool.acquire() as conn:
        view_name = await conn.fetchval(
            "SELECT view_name "
            "FROM timescaledb_information.continuous_aggregates "
            "WHERE view_name = 'tag_readings_1min'"
        )
        assert view_name == "tag_readings_1min"

        raw = await conn.fetchval(
            "SELECT config FROM timescaledb_information.jobs "
            "WHERE proc_name = 'policy_refresh_continuous_aggregate' "
            "  AND hypertable_name = 'tag_readings_1min'"
        )
        assert raw is not None, "1min refresh policy yok"
        config = _parse_config(raw)
        assert _interval_matches(
            config.get("start_offset"), ["3 hours", "03:00:00"]
        ), f"1min start_offset beklenmedik: {config.get('start_offset')!r}"
        assert _interval_matches(
            config.get("end_offset"), ["1 min", "1 minute", "00:01:00"]
        ), f"1min end_offset beklenmedik: {config.get('end_offset')!r}"


@pytest.mark.usefixtures("_check_db_available")
async def test_1hour_aggregate_exists_and_has_policy(
    db: TimescaleDBDatabase,
) -> None:
    """tag_readings_1hour CA kayıtlı ve refresh policy var."""
    pool = db._get_pool()
    async with pool.acquire() as conn:
        view_name = await conn.fetchval(
            "SELECT view_name "
            "FROM timescaledb_information.continuous_aggregates "
            "WHERE view_name = 'tag_readings_1hour'"
        )
        assert view_name == "tag_readings_1hour"

        raw = await conn.fetchval(
            "SELECT config FROM timescaledb_information.jobs "
            "WHERE proc_name = 'policy_refresh_continuous_aggregate' "
            "  AND hypertable_name = 'tag_readings_1hour'"
        )
        assert raw is not None, "1hour refresh policy yok"
        config = _parse_config(raw)
        assert _interval_matches(
            config.get("start_offset"), ["1 day", "1 day 00:00:00", "24:00:00"]
        ), f"1hour start_offset beklenmedik: {config.get('start_offset')!r}"
        assert _interval_matches(
            config.get("end_offset"), ["1 hour", "01:00:00"]
        ), f"1hour end_offset beklenmedik: {config.get('end_offset')!r}"


@pytest.mark.usefixtures("_check_db_available")
async def test_1min_retention_3_years(
    db: TimescaleDBDatabase,
) -> None:
    """tag_readings_1min için 3 yıllık retention policy kayıtlı."""
    pool = db._get_pool()
    async with pool.acquire() as conn:
        raw = await conn.fetchval(
            "SELECT config FROM timescaledb_information.jobs "
            "WHERE proc_name = 'policy_retention' "
            "  AND hypertable_name = 'tag_readings_1min'"
        )
        assert raw is not None, "1min retention policy yok"
        config = _parse_config(raw)
        assert config.get("drop_after") == "3 years", (
            f"1min drop_after beklenen '3 years', alınan: "
            f"{config.get('drop_after')!r}"
        )


@pytest.mark.usefixtures("_check_db_available")
async def test_1hour_has_no_retention(
    db: TimescaleDBDatabase,
) -> None:
    """tag_readings_1hour için retention policy YOK (sınırsız saklama)."""
    pool = db._get_pool()
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM timescaledb_information.jobs "
            "WHERE proc_name = 'policy_retention' "
            "  AND hypertable_name = 'tag_readings_1hour'"
        )
        assert count == 0, (
            "1hour için retention policy olmamalıydı, sınırsız saklanır"
        )


@pytest.mark.usefixtures("_check_db_available")
async def test_aggregate_returns_correct_avg(
    db: TimescaleDBDatabase,
) -> None:
    """Bilinen veri insert + refresh + bucket AVG/MIN/MAX/COUNT doğrulama.

    60 saniye boyunca value = 1, 2, ..., 60 eklenir. 1 dakikalık bucket'ın
    AVG = 30.5, MIN = 1, MAX = 60, COUNT = 60 olmalı.
    """
    unique = uuid.uuid4().hex[:8]
    tag_id = f"TEST_CA_{unique}"
    await db.insert_tag(TagRecord(
        tag_id=tag_id, name="CA Avg Test",
        modbus_host="127.0.0.1", register_address=40100,
    ))

    # Dakikanın başına hizalı bir bucket seç (2 dk geri — real-time penceresinden uzak)
    now = datetime.now(UTC).replace(microsecond=0)
    bucket_start = now.replace(second=0) - timedelta(minutes=2)

    batch = [
        TagReading(
            timestamp=bucket_start + timedelta(seconds=i),
            tag_id=tag_id,
            value=float(i + 1),  # 1, 2, ..., 60
            quality_flag=0,
        )
        for i in range(60)
    ]
    await db.insert_tag_readings_batch(batch)

    pool = db._get_pool()
    async with pool.acquire() as conn:
        # CALL refresh_continuous_aggregate açık transaction içinde çalışamaz;
        # asyncpg'nin implicit txn'ını kapat, sonra çağır.
        await conn.execute("COMMIT;")
        # PROCEDURE parametre tipleri çıkarılamadığı için explicit cast zorunlu.
        await conn.execute(
            "CALL refresh_continuous_aggregate("
            "    'tag_readings_1min', $1::timestamptz, $2::timestamptz"
            ");",
            bucket_start,
            bucket_start + timedelta(minutes=2),
        )

        row = await conn.fetchrow(
            "SELECT avg_value, min_value, max_value, sample_count "
            "FROM tag_readings_1min "
            "WHERE tag_id = $1 AND bucket = $2",
            tag_id,
            bucket_start,
        )

    assert row is not None, "1min agregatında bucket bulunamadı"
    # value 1..60 → AVG = 30.5, MIN = 1, MAX = 60, COUNT = 60
    assert abs(float(row["avg_value"]) - 30.5) < 0.001, (
        f"avg_value beklenen 30.5, alınan: {row['avg_value']}"
    )
    assert float(row["min_value"]) == 1.0
    assert float(row["max_value"]) == 60.0
    assert int(row["sample_count"]) == 60


@pytest.mark.usefixtures("_check_db_available")
async def test_1hour_aggregate_derived_from_1min(
    db: TimescaleDBDatabase,
) -> None:
    """1hour CA'nın view tanımı 1min'i referans etmeli (hierarchical yapı)."""
    pool = db._get_pool()
    async with pool.acquire() as conn:
        view_def = await conn.fetchval(
            "SELECT view_definition "
            "FROM timescaledb_information.continuous_aggregates "
            "WHERE view_name = 'tag_readings_1hour'"
        )
        assert view_def is not None
        assert "tag_readings_1min" in view_def, (
            "tag_readings_1hour view_definition içinde 'tag_readings_1min' "
            f"beklenmişti — hierarchical CA kurulmamış. Bulunan: {view_def!r}"
        )
