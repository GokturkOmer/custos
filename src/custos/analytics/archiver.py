"""Parquet aylık arşiv job'u (F11 Paket E).

Her ayın 1'inde 02:00 TRT'de otomatik çalışır; bir önceki ayın tüm verisini
üç ayrı Parquet dosyasına yazar: ham ``tag_readings``, dakika agregat
``tag_readings_1min``, saat agregat ``tag_readings_1hour``.

Amaç: "verim dosyada elimde, TimescaleDB bir gün gitse bile kalır" güvencesi.
Apache Arrow ekosistemi uzun vadeli bir taahhüt; LZ4 sıkıştırma hem hız hem
kapasite dengesi için makul.

Stream pattern: ``DatabaseInterface.stream_*`` async generator'ları server-side
cursor kullanır — milyonlarca satırı belleğe yüklemeden batch batch yazar.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq
import structlog

from custos.shared.database import DatabaseInterface

logger = structlog.get_logger(logger_name="archiver")

# TRT (Türkiye Standart Saati) — ay sınırları bu zaman diliminde hesaplanır.
_TRT = ZoneInfo("Europe/Istanbul")

# PyArrow şemaları — tüm kolonlar explicit tiplenir ki Parquet metadata'sı
# düzgün olsun ve okuyan tarafta sürpriz dönüşüm olmasın.
_RAW_SCHEMA = pa.schema(
    [
        ("timestamp", pa.timestamp("us", tz="UTC")),
        ("tag_id", pa.string()),
        ("value", pa.float64()),
        ("quality_flag", pa.int16()),
    ]
)

_AGG_SCHEMA = pa.schema(
    [
        ("bucket", pa.timestamp("us", tz="UTC")),
        ("tag_id", pa.string()),
        ("avg_value", pa.float64()),
        ("min_value", pa.float64()),
        ("max_value", pa.float64()),
        ("stddev_value", pa.float64()),
        ("max_quality", pa.int16()),
        ("sample_count", pa.int64()),
    ]
)


@dataclass
class ArchiveResult:
    """Tek aylık arşivleme sonucunun özeti.

    Endpoint ve scheduler log'u bunu kullanır; dosya boyutları pilot
    operatörüne disk planlaması için görünür olur.
    """

    year: int
    month: int
    raw_rows: int = 0
    raw_file_bytes: int = 0
    agg_1min_rows: int = 0
    agg_1min_file_bytes: int = 0
    agg_1hour_rows: int = 0
    agg_1hour_file_bytes: int = 0
    duration_seconds: float = 0.0
    output_dir: Path = field(default_factory=lambda: Path("."))


def _month_bounds_utc(year: int, month: int) -> tuple[datetime, datetime]:
    """TRT takvim ayının UTC sınırlarını döndürür.

    Dönüş ``[start, end)`` yarı-açık aralık; sorgular bu sınırlarla
    `>= start AND < end` kullanır. DST geçişleri Europe/Istanbul için
    yok (sabit UTC+3) ama kod gelecekteki değişikliğe dayanıklı kalsın
    diye yine zoneinfo ile yapılır.
    """
    if not 1 <= month <= 12:
        msg = f"Geçersiz ay: {month} (1..12 olmalı)"
        raise ValueError(msg)
    start_local = datetime(year, month, 1, 0, 0, 0, tzinfo=_TRT)
    if month == 12:
        end_local = datetime(year + 1, 1, 1, 0, 0, 0, tzinfo=_TRT)
    else:
        end_local = datetime(year, month + 1, 1, 0, 0, 0, tzinfo=_TRT)
    return start_local.astimezone(UTC), end_local.astimezone(UTC)


def _previous_month(reference: datetime) -> tuple[int, int]:
    """Verilen UTC zamanın bir önceki TRT takvim ayını (year, month) olarak döndürür."""
    local = reference.astimezone(_TRT)
    first_of_this_month = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    prev = first_of_this_month - timedelta(days=1)
    return prev.year, prev.month


class ParquetArchiver:
    """Aylık Parquet arşiv yazıcısı.

    - ``archive_month(year, month)`` belirtilen TRT takvim ayını arşivler,
      idempotenttir (dosya varsa üzerine yazılır).
    - ``run_scheduled()`` scheduler'dan çağrılır; bir önceki ayı arşivler.
    """

    def __init__(self, db: DatabaseInterface, archive_dir: Path) -> None:
        self._db = db
        self._archive_dir = archive_dir

    @property
    def archive_dir(self) -> Path:
        """Kök arşiv dizini (testten görünür)."""
        return self._archive_dir

    async def archive_month(self, year: int, month: int) -> ArchiveResult:
        """Belirtilen TRT takvim ayını üç Parquet dosyası olarak yazar.

        Dosya yolu: ``<archive_dir>/YYYY-MM/{tag_readings,tag_readings_1min,
        tag_readings_1hour}.parquet``. Varsa overwrite; partial bir yazım
        exception ile iptal olursa yarım dosya kalabilir — bu durumda tekrar
        çalıştırmak idempotent şekilde temizler.
        """
        start_utc, end_utc = _month_bounds_utc(year, month)
        month_dir = self._archive_dir / f"{year:04d}-{month:02d}"
        month_dir.mkdir(parents=True, exist_ok=True)

        await logger.ainfo(
            "Parquet arşiv başlıyor",
            year=year,
            month=month,
            start_utc=start_utc.isoformat(),
            end_utc=end_utc.isoformat(),
            output_dir=str(month_dir),
        )

        t0 = time.monotonic()
        result = ArchiveResult(year=year, month=month, output_dir=month_dir)

        raw_path = month_dir / "tag_readings.parquet"
        raw_rows = await _write_stream_to_parquet(
            self._db.stream_raw_readings(start_utc, end_utc),
            raw_path,
            _RAW_SCHEMA,
        )
        result.raw_rows = raw_rows
        result.raw_file_bytes = raw_path.stat().st_size

        min_path = month_dir / "tag_readings_1min.parquet"
        agg_1min_rows = await _write_stream_to_parquet(
            self._db.stream_1min_aggregates(start_utc, end_utc),
            min_path,
            _AGG_SCHEMA,
        )
        result.agg_1min_rows = agg_1min_rows
        result.agg_1min_file_bytes = min_path.stat().st_size

        hour_path = month_dir / "tag_readings_1hour.parquet"
        agg_1hour_rows = await _write_stream_to_parquet(
            self._db.stream_1hour_aggregates(start_utc, end_utc),
            hour_path,
            _AGG_SCHEMA,
        )
        result.agg_1hour_rows = agg_1hour_rows
        result.agg_1hour_file_bytes = hour_path.stat().st_size

        result.duration_seconds = time.monotonic() - t0
        await logger.ainfo(
            "Parquet arşiv tamamlandı",
            year=year,
            month=month,
            raw_rows=result.raw_rows,
            agg_1min_rows=result.agg_1min_rows,
            agg_1hour_rows=result.agg_1hour_rows,
            total_bytes=(
                result.raw_file_bytes + result.agg_1min_file_bytes + result.agg_1hour_file_bytes
            ),
            duration_seconds=round(result.duration_seconds, 3),
        )
        return result

    async def run_scheduled(self) -> ArchiveResult:
        """Scheduler'dan çağrılır — şu andan bir önceki TRT ayını arşivler."""
        now_utc = datetime.now(UTC)
        year, month = _previous_month(now_utc)
        return await self.archive_month(year, month)


async def _write_stream_to_parquet(
    stream: AsyncIterator[list[dict[str, Any]]],
    path: Path,
    schema: pa.Schema,
) -> int:
    """Async row-dict stream'ini LZ4 sıkıştırılmış Parquet'e yazar.

    Boş stream durumunda bile şema ile imzalı geçerli bir Parquet dosyası
    üretilir (0 satırlı). Bu, tüketici tarafının dosya varlığına güvenmesini
    sağlar.
    """
    total = 0
    with pq.ParquetWriter(path, schema, compression="lz4") as writer:
        async for batch in stream:
            if not batch:
                continue
            table = pa.Table.from_pylist(batch, schema=schema)
            writer.write_table(table)
            total += len(batch)
    return total
