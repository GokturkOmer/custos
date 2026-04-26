"""query_tag_readings_downsampled integration testi.

TimescaleDB time_bucket ile çalışır — hypertable üzerinde AVG aggregation.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from custos.shared.database import (
    TagReading,
    TagRecord,
    TimescaleDBDatabase,
)


async def _seed_readings(
    db: TimescaleDBDatabase,
    tag_id: str,
    start: datetime,
    count: int,
    step_seconds: float = 1.0,
) -> None:
    """Test için N adet okuma insert eder (her biri step_seconds arayla)."""
    batch: list[TagReading] = []
    for i in range(count):
        ts = start + timedelta(seconds=i * step_seconds)
        batch.append(
            TagReading(
                timestamp=ts,
                tag_id=tag_id,
                value=float(i),
                quality_flag=0,
            )
        )
    await db.insert_tag_readings_batch(batch)


@pytest.mark.usefixtures("_check_db_available")
async def test_downsample_reduces_to_target_points(
    db: TimescaleDBDatabase,
) -> None:
    """1000 nokta → target 100 ile ~100 bucket döndürmeli."""
    unique = uuid.uuid4().hex[:8]
    tag_id = f"TEST_DS_{unique}"
    await db.insert_tag(
        TagRecord(
            tag_id=tag_id,
            name="Downsample Test",
            modbus_host="127.0.0.1",
            register_address=40001,
        )
    )

    start = datetime.now(UTC) - timedelta(hours=1)
    await _seed_readings(db, tag_id, start, count=1000, step_seconds=3.6)
    end = datetime.now(UTC)

    result = await db.query_tag_readings_downsampled(
        tag_id,
        start,
        end,
        target_points=100,
    )
    # Gerçek sayı target'a yakın olmalı (bucket boyutu (3600/100)=36s)
    assert 50 <= len(result) <= 120


@pytest.mark.usefixtures("_check_db_available")
async def test_downsample_bucket_value_is_average(
    db: TimescaleDBDatabase,
) -> None:
    """Bucket değeri içindeki okumaların AVG'si olmalı.

    Sabit değer (5.0) kullanıyoruz; TimescaleDB time_bucket data aralığını
    epoch'a hizaladığından aralık birden fazla bucket'a düşebilir — bu
    durumda her bucket'ın AVG'si de 5.0 olmalı.
    """
    unique = uuid.uuid4().hex[:8]
    tag_id = f"TEST_DS_AVG_{unique}"
    await db.insert_tag(
        TagRecord(
            tag_id=tag_id,
            name="Avg Test",
            modbus_host="127.0.0.1",
            register_address=40002,
        )
    )

    start = datetime.now(UTC) - timedelta(seconds=30)
    # Tüm okumalar 5.0 — her bucket'ın AVG'si 5.0 olmalı
    batch = [
        TagReading(
            timestamp=start + timedelta(seconds=i),
            tag_id=tag_id,
            value=5.0,
            quality_flag=0,
        )
        for i in range(30)
    ]
    await db.insert_tag_readings_batch(batch)
    end = datetime.now(UTC)

    result = await db.query_tag_readings_downsampled(
        tag_id,
        start,
        end,
        target_points=10,
    )
    assert len(result) >= 1
    assert all(r.value == 5.0 for r in result)


@pytest.mark.usefixtures("_check_db_available")
async def test_downsample_empty_range_returns_empty(
    db: TimescaleDBDatabase,
) -> None:
    """Data olmayan aralık → boş liste."""
    unique = uuid.uuid4().hex[:8]
    tag_id = f"TEST_DS_EMPTY_{unique}"
    await db.insert_tag(
        TagRecord(
            tag_id=tag_id,
            name="Empty Test",
            modbus_host="127.0.0.1",
            register_address=40003,
        )
    )

    # Data yok — sadece tag tanımlı
    start = datetime.now(UTC) - timedelta(hours=1)
    end = datetime.now(UTC)
    result = await db.query_tag_readings_downsampled(
        tag_id,
        start,
        end,
        target_points=100,
    )
    assert result == []


@pytest.mark.usefixtures("_check_db_available")
async def test_downsample_preserves_order(
    db: TimescaleDBDatabase,
) -> None:
    """Sonuç bucket timestamp'e göre sıralı gelmeli."""
    unique = uuid.uuid4().hex[:8]
    tag_id = f"TEST_DS_ORDER_{unique}"
    await db.insert_tag(
        TagRecord(
            tag_id=tag_id,
            name="Order Test",
            modbus_host="127.0.0.1",
            register_address=40004,
        )
    )

    start = datetime.now(UTC) - timedelta(minutes=10)
    await _seed_readings(db, tag_id, start, count=100, step_seconds=6.0)
    end = datetime.now(UTC)

    result = await db.query_tag_readings_downsampled(
        tag_id,
        start,
        end,
        target_points=20,
    )
    assert len(result) >= 2
    timestamps = [r.timestamp for r in result]
    assert timestamps == sorted(timestamps)
