"""ParquetArchiver integration testleri (F11 Paket E).

Arşivleyici TRT takvim ayını üç Parquet dosyasına yazar:
- ``tag_readings.parquet`` (ham)
- ``tag_readings_1min.parquet`` (dakika agregat)
- ``tag_readings_1hour.parquet`` (saat agregat)

Testler küçük ölçekte (3 satır ham / 1 bucket agregat) çalışır; amaç şema,
idempotent yazım, endpoint akışı ve satır sayısı eşleşmesini doğrulamaktır.
Büyük ölçekli performans test'i kapsam dışı (F11 Paket E sonrası smoke test).

Manuel endpoint testi FastAPI TestClient üzerinden gider ve mock archiver
kullanır — gerçek DB'ye dokunmaz, böylece ``_check_db_available`` fixture'ına
ihtiyaç duymaz.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

from custos.analytics.archiver import ArchiveResult, ParquetArchiver
from custos.analytics.dashboard.app import router
from custos.shared.database import (
    TagReading,
    TagRecord,
    TimescaleDBDatabase,
)

# --- Ortak yardımcılar ---


async def _refresh_1min(
    db: TimescaleDBDatabase,
    start: datetime,
    end: datetime,
) -> None:
    """1min CA'yı verilen aralık için manuel refresh eder."""
    pool = db._get_pool()
    async with pool.acquire() as conn:
        await conn.execute("COMMIT;")
        await conn.execute(
            "CALL refresh_continuous_aggregate("
            "    'tag_readings_1min', $1::timestamptz, $2::timestamptz"
            ");",
            start,
            end,
        )


async def _refresh_1hour(
    db: TimescaleDBDatabase,
    start: datetime,
    end: datetime,
) -> None:
    """1hour CA'yı verilen aralık için manuel refresh eder."""
    pool = db._get_pool()
    async with pool.acquire() as conn:
        await conn.execute("COMMIT;")
        await conn.execute(
            "CALL refresh_continuous_aggregate("
            "    'tag_readings_1hour', $1::timestamptz, $2::timestamptz"
            ");",
            start,
            end,
        )


async def _seed_month(
    db: TimescaleDBDatabase,
    tag_id: str,
    year: int,
    month: int,
) -> tuple[int, datetime, datetime]:
    """Verilen TRT ayı içine 3 satır insert eder ve iki agregatı refresh eder.

    Dönüş: (insert edilen satır sayısı, ay_start_utc, ay_end_utc).
    """
    from zoneinfo import ZoneInfo

    trt = ZoneInfo("Europe/Istanbul")
    start_local = datetime(year, month, 1, 0, 0, 0, tzinfo=trt)
    if month == 12:
        end_local = datetime(year + 1, 1, 1, tzinfo=trt)
    else:
        end_local = datetime(year, month + 1, 1, tzinfo=trt)
    start_utc = start_local.astimezone(UTC)
    end_utc = end_local.astimezone(UTC)

    # Ay ortasında 3 satır — agregat bucket'ları doldurmak için peş peşe saniye.
    base = start_utc + timedelta(days=14, hours=3)
    batch = [
        TagReading(
            timestamp=base + timedelta(seconds=i),
            tag_id=tag_id,
            value=10.0 + i,
            quality_flag=0,
        )
        for i in range(3)
    ]
    await db.insert_tag_readings_batch(batch)

    await _refresh_1min(db, base - timedelta(minutes=1), base + timedelta(minutes=2))
    await _refresh_1hour(db, base - timedelta(hours=1), base + timedelta(hours=2))
    return len(batch), start_utc, end_utc


# --- Archiver testleri (gerçek DB) ---


@pytest.mark.usefixtures("_check_db_available")
async def test_archive_month_writes_three_files(
    db: TimescaleDBDatabase,
    tmp_path: Path,
) -> None:
    """Archiver belirtilen ay için 3 Parquet dosyası oluşturmalı."""
    unique = uuid.uuid4().hex[:8]
    tag_id = f"TEST_ARC_{unique}"
    await db.insert_tag(
        TagRecord(
            tag_id=tag_id,
            name="Archive Test",
            modbus_host="127.0.0.1",
            register_address=40800,
        )
    )
    await _seed_month(db, tag_id, 2025, 6)

    archiver = ParquetArchiver(db=db, archive_dir=tmp_path)
    result = await archiver.archive_month(2025, 6)

    month_dir = tmp_path / "2025-06"
    assert (month_dir / "tag_readings.parquet").is_file()
    assert (month_dir / "tag_readings_1min.parquet").is_file()
    assert (month_dir / "tag_readings_1hour.parquet").is_file()
    assert result.output_dir == month_dir
    assert result.raw_file_bytes > 0
    assert result.agg_1min_file_bytes > 0
    assert result.agg_1hour_file_bytes > 0


@pytest.mark.usefixtures("_check_db_available")
async def test_archive_month_is_idempotent(
    db: TimescaleDBDatabase,
    tmp_path: Path,
) -> None:
    """Aynı ay iki kez çağrılırsa ikinci çalışma dosyayı üzerine yazar (hata vermez)."""
    unique = uuid.uuid4().hex[:8]
    tag_id = f"TEST_ARC_{unique}"
    await db.insert_tag(
        TagRecord(
            tag_id=tag_id,
            name="Archive Idempotent",
            modbus_host="127.0.0.1",
            register_address=40801,
        )
    )
    await _seed_month(db, tag_id, 2025, 7)

    archiver = ParquetArchiver(db=db, archive_dir=tmp_path)
    first = await archiver.archive_month(2025, 7)
    second = await archiver.archive_month(2025, 7)

    assert first.raw_rows == second.raw_rows
    # İkinci yazım yeni dosya oluşturmalı (mtime update) ama path aynı kalmalı.
    raw_path = tmp_path / "2025-07" / "tag_readings.parquet"
    assert raw_path.is_file()


@pytest.mark.usefixtures("_check_db_available")
async def test_archive_result_row_counts_match_db(
    db: TimescaleDBDatabase,
    tmp_path: Path,
) -> None:
    """ArchiveResult.raw_rows veritabanındaki ay içindeki satır sayısına eşit olmalı."""
    unique = uuid.uuid4().hex[:8]
    tag_id = f"TEST_ARC_{unique}"
    await db.insert_tag(
        TagRecord(
            tag_id=tag_id,
            name="Archive Count",
            modbus_host="127.0.0.1",
            register_address=40802,
        )
    )
    inserted, start_utc, end_utc = await _seed_month(db, tag_id, 2025, 8)

    archiver = ParquetArchiver(db=db, archive_dir=tmp_path)
    result = await archiver.archive_month(2025, 8)

    pool = db._get_pool()
    async with pool.acquire() as conn:
        db_raw_count = await conn.fetchval(
            "SELECT COUNT(*) FROM tag_readings "
            "WHERE tag_id = $1 AND timestamp >= $2 AND timestamp < $3",
            tag_id,
            start_utc,
            end_utc,
        )
    # Diğer paralel testler de bu aya yazmış olabilir; TEST_ARC_ prefix'li
    # 3 okumamız dahil olmak üzere tüm satırlar dahildir.
    assert result.raw_rows >= inserted
    assert db_raw_count is not None
    assert result.raw_rows >= int(db_raw_count)


@pytest.mark.usefixtures("_check_db_available")
async def test_parquet_can_be_read_back(
    db: TimescaleDBDatabase,
    tmp_path: Path,
) -> None:
    """Yazılan Parquet ``pq.read_table`` ile okunabilmeli ve satır sayısı eşleşmeli."""
    unique = uuid.uuid4().hex[:8]
    tag_id = f"TEST_ARC_{unique}"
    await db.insert_tag(
        TagRecord(
            tag_id=tag_id,
            name="Archive Readback",
            modbus_host="127.0.0.1",
            register_address=40803,
        )
    )
    await _seed_month(db, tag_id, 2025, 9)

    archiver = ParquetArchiver(db=db, archive_dir=tmp_path)
    result = await archiver.archive_month(2025, 9)

    raw_path = tmp_path / "2025-09" / "tag_readings.parquet"
    table = pq.read_table(raw_path)
    assert table.num_rows == result.raw_rows
    col_names = set(table.schema.names)
    assert {"timestamp", "tag_id", "value", "quality_flag"} <= col_names

    # Bizim tag'imizin 3 satırı var mı?
    df = table.to_pylist()
    mine = [r for r in df if r["tag_id"] == tag_id]
    assert len(mine) == 3
    assert sorted(r["value"] for r in mine) == [10.0, 11.0, 12.0]


# --- Endpoint testi (mock archiver, DB gerekmez) ---


class _FakeArchiver:
    """archive_month çağrılarını toplayan sahte archiver."""

    def __init__(self) -> None:
        self.calls: list[tuple[int, int]] = []

    async def archive_month(self, year: int, month: int) -> ArchiveResult:
        self.calls.append((year, month))
        return ArchiveResult(
            year=year,
            month=month,
            raw_rows=42,
            raw_file_bytes=1024,
            agg_1min_rows=7,
            agg_1min_file_bytes=256,
            agg_1hour_rows=1,
            agg_1hour_file_bytes=128,
            duration_seconds=0.05,
            output_dir=Path(f"/tmp/mock/{year:04d}-{month:02d}"),
        )


def _build_test_app(archiver: _FakeArchiver) -> TestClient:
    """Router'ı tek başına yükleyen, state.archiver = archiver olan TestClient.

    V11-101 sonrası ``/api/archive/run`` route'u developer-only oldu;
    minimal app'te ``require_developer`` dependency override ile bypass
    edilir (gerçek session/cookie yok).
    """
    from datetime import UTC
    from datetime import datetime as _dt

    from fastapi import FastAPI

    from custos.analytics.dashboard.auth_dependencies import require_developer
    from custos.shared.database import Session

    app = FastAPI()
    app.include_router(router)
    app.state.archiver = archiver
    fake_session = Session(
        id=1,
        user_id=1,
        username="test_dev",
        role="developer",
        enabled=True,
        must_change_password=False,
        expires_at=_dt(2099, 1, 1, tzinfo=UTC),
    )
    app.dependency_overrides[require_developer] = lambda: fake_session
    return TestClient(app)


def test_manual_endpoint_triggers_archive() -> None:
    """POST /dashboard/api/archive/run mock archiver'ı çağırmalı ve 200 dönmeli."""
    archiver = _FakeArchiver()
    client = _build_test_app(archiver)
    resp = client.post("/dashboard/api/archive/run?year=2025&month=3")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["year"] == 2025
    assert body["month"] == 3
    assert body["raw_rows"] == 42
    assert archiver.calls == [(2025, 3)]


def test_manual_endpoint_rejects_invalid_month() -> None:
    """Ay 1..12 dışındaysa 400 dönmeli."""
    archiver = _FakeArchiver()
    client = _build_test_app(archiver)
    resp = client.post("/dashboard/api/archive/run?year=2025&month=13")
    assert resp.status_code == 400
    assert archiver.calls == []


async def test_manual_endpoint_conflict_when_lock_held() -> None:
    """Kilit başka bir iş tarafından tutuluyorsa 409 dönmeli.

    httpx.AsyncClient ile aynı event loop'ta iki istek eşzamanlı atılır;
    yavaşlatılmış archiver birinciyi kilidi tutarken beklemeye zorlar,
    ikinci istek 409 almalıdır.
    """
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    class SlowArchiver(_FakeArchiver):
        async def archive_month(self, year: int, month: int) -> ArchiveResult:
            await asyncio.sleep(0.2)
            return await super().archive_month(year, month)

    from datetime import UTC
    from datetime import datetime as _dt

    from custos.analytics.dashboard.auth_dependencies import require_developer
    from custos.shared.database import Session

    slow = SlowArchiver()
    app = FastAPI()
    app.include_router(router)
    app.state.archiver = slow
    fake_session = Session(
        id=1,
        user_id=1,
        username="test_dev",
        role="developer",
        enabled=True,
        must_change_password=False,
        expires_at=_dt(2099, 1, 1, tzinfo=UTC),
    )
    app.dependency_overrides[require_developer] = lambda: fake_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Eşzamanlı iki istek — birincisi kilidi alır, ikincisi 409 görür.
        task_a = asyncio.create_task(
            client.post("/dashboard/api/archive/run?year=2025&month=4"),
        )
        # İkinciyi kısa gecikmeyle at ki birincisi kilidi alma şansına sahip olsun.
        await asyncio.sleep(0.05)
        task_b = asyncio.create_task(
            client.post("/dashboard/api/archive/run?year=2025&month=5"),
        )
        resp_a, resp_b = await asyncio.gather(task_a, task_b)

    codes = sorted([resp_a.status_code, resp_b.status_code])
    assert codes == [200, 409], f"Beklenen [200, 409], alınan: {codes}"


# --- Yardımcı: stream iteratörü testi (küçük smoke) ---


@pytest.mark.usefixtures("_check_db_available")
async def test_stream_raw_readings_returns_all_inserted(
    db: TimescaleDBDatabase,
) -> None:
    """stream_raw_readings cursor üzerinden tüm satırları döndürmeli."""
    unique = uuid.uuid4().hex[:8]
    tag_id = f"TEST_ARC_{unique}"
    await db.insert_tag(
        TagRecord(
            tag_id=tag_id,
            name="Stream Test",
            modbus_host="127.0.0.1",
            register_address=40804,
        )
    )
    start = datetime(2024, 3, 15, 10, 0, 0, tzinfo=UTC)
    batch = [
        TagReading(
            timestamp=start + timedelta(seconds=i),
            tag_id=tag_id,
            value=float(i),
            quality_flag=0,
        )
        for i in range(25)
    ]
    await db.insert_tag_readings_batch(batch)

    collected: list[dict[str, Any]] = []
    stream: AsyncIterator[list[dict[str, Any]]] = db.stream_raw_readings(
        start,
        start + timedelta(minutes=1),
        batch_size=10,
    )
    async for chunk in stream:
        collected.extend(chunk)

    mine = [r for r in collected if r["tag_id"] == tag_id]
    assert len(mine) == 25
    assert mine[0]["value"] == 0.0
    assert mine[-1]["value"] == 24.0
