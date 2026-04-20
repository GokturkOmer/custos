"""TimescaleDB hypertable hardening doğrulama script'i.

Kullanım: python scripts/verify_timescale_policies.py

Beklenen ayarlar (F11 Paket A migration 024):
- Chunk interval: 1 gün
- Compression: enabled, segmentby='tag_id', orderby='timestamp DESC'
- Compression policy: 7 gün sonra
- Retention policy: 365 gün

Hypertable'lar: tag_readings, features.

Exit kodu:
- 0: Tüm kontroller başarılı
- 1: Bir veya daha fazla kontrol başarısız (veya DB erişim hatası)
"""

import asyncio
import json
import sys
from typing import Any, cast

import asyncpg

from custos.shared.config import settings

HYPERTABLES: tuple[str, ...] = ("tag_readings", "features")
EXPECTED_CHUNK_INTERVAL_US: int = 86_400_000_000  # 1 gün (mikrosaniye)
EXPECTED_COMPRESSION_AFTER_DAYS: int = 7
EXPECTED_RETENTION_DAYS: int = 365


def _fmt_status(ok: bool) -> str:
    """Başarılıysa OK, değilse FAIL döndür."""
    return "OK  " if ok else "FAIL"


async def _check_chunk_interval(
    conn: asyncpg.Connection, hypertable: str
) -> tuple[bool, str]:
    """Hypertable'ın chunk interval'ını mikrosaniye olarak doğrular."""
    row = await conn.fetchrow(
        """
        SELECT time_interval
        FROM timescaledb_information.dimensions
        WHERE hypertable_name = $1
        """,
        hypertable,
    )
    if row is None:
        return False, "dimension bilgisi bulunamadı"
    interval = row["time_interval"]
    interval_us = int(interval.total_seconds() * 1_000_000)
    ok = interval_us == EXPECTED_CHUNK_INTERVAL_US
    return ok, f"chunk_interval={interval}"


async def _check_compression_enabled(
    conn: asyncpg.Connection, hypertable: str
) -> tuple[bool, str]:
    """Compression açık ve segmentby='tag_id', orderby='timestamp DESC' mi.

    TimescaleDB 2.x'te compression_settings görünümü her segmentby/orderby
    kolonu için ayrı satır döndürür: attname + segmentby_column_index +
    orderby_column_index + orderby_asc.
    """
    enabled_row = await conn.fetchrow(
        """
        SELECT compression_enabled
        FROM timescaledb_information.hypertables
        WHERE hypertable_name = $1
        """,
        hypertable,
    )
    if enabled_row is None:
        return False, "hypertable bilgisi bulunamadı"
    if not enabled_row["compression_enabled"]:
        return False, "compression_enabled=False"

    rows = await conn.fetch(
        """
        SELECT attname, segmentby_column_index, orderby_column_index,
               orderby_asc
        FROM timescaledb_information.compression_settings
        WHERE hypertable_name = $1
        """,
        hypertable,
    )
    if not rows:
        return False, "compression_settings boş"

    segmentby_cols = [
        r["attname"] for r in rows if r["segmentby_column_index"] is not None
    ]
    orderby_entries = [
        (r["attname"], r["orderby_asc"])
        for r in rows
        if r["orderby_column_index"] is not None
    ]

    if segmentby_cols != ["tag_id"]:
        return False, f"segmentby={segmentby_cols!r} (beklenen ['tag_id'])"
    if orderby_entries != [("timestamp", False)]:
        return (
            False,
            f"orderby={orderby_entries!r} "
            "(beklenen [('timestamp', False)] = timestamp DESC)",
        )
    return True, "segmentby=tag_id, orderby=timestamp DESC"


def _parse_config(raw: Any) -> dict[str, Any]:
    """timescaledb_information.jobs.config bazen str bazen dict döndürür."""
    if isinstance(raw, dict):
        return cast(dict[str, Any], raw)
    if isinstance(raw, str):
        return cast(dict[str, Any], json.loads(raw))
    return {}


async def _check_compression_policy(
    conn: asyncpg.Connection, hypertable: str
) -> tuple[bool, str]:
    """7 günlük compression policy var mı doğrular."""
    row = await conn.fetchrow(
        """
        SELECT j.config
        FROM timescaledb_information.jobs j
        WHERE j.proc_name = 'policy_compression'
          AND j.hypertable_name = $1
        """,
        hypertable,
    )
    if row is None:
        return False, "compression policy yok"
    config = _parse_config(row["config"])
    after = config.get("compress_after")
    expected = f"{EXPECTED_COMPRESSION_AFTER_DAYS} days"
    if after != expected:
        return False, f"compress_after={after!r} (beklenen {expected!r})"
    return True, f"compress_after={after}"


async def _check_retention_policy(
    conn: asyncpg.Connection, hypertable: str
) -> tuple[bool, str]:
    """365 günlük retention policy var mı doğrular."""
    row = await conn.fetchrow(
        """
        SELECT j.config
        FROM timescaledb_information.jobs j
        WHERE j.proc_name = 'policy_retention'
          AND j.hypertable_name = $1
        """,
        hypertable,
    )
    if row is None:
        return False, "retention policy yok"
    config = _parse_config(row["config"])
    after = config.get("drop_after")
    expected = f"{EXPECTED_RETENTION_DAYS} days"
    if after != expected:
        return False, f"drop_after={after!r} (beklenen {expected!r})"
    return True, f"drop_after={after}"


async def _compression_stats(
    conn: asyncpg.Connection, hypertable: str
) -> str:
    """Bilgi amaçlı: kaç chunk sıkıştırılmış, oran ne?

    Henüz hiç chunk sıkıştırılmamış olabilir (taze migration). Bu kontrol
    başarı/başarısızlık üretmez, sadece bilgi yazdırır.
    """
    row = await conn.fetchrow(
        """
        SELECT
            COALESCE(SUM(CASE WHEN is_compressed THEN 1 ELSE 0 END), 0)
                AS compressed_chunks,
            COUNT(*) AS total_chunks
        FROM timescaledb_information.chunks
        WHERE hypertable_name = $1
        """,
        hypertable,
    )
    if row is None:
        return "chunk bilgisi yok"
    return (
        f"compressed={row['compressed_chunks']}/{row['total_chunks']} chunk"
    )


async def main() -> int:
    """Tüm hypertable'lar için 4 kontrolü çalıştır, tablo formatında raporla."""
    try:
        conn = await asyncpg.connect(dsn=settings.database_url)
    except Exception as exc:
        print(f"FAIL: Veritabanına bağlanılamadı — {exc}")  # noqa: T201
        return 1

    all_ok = True
    try:
        header = (
            f"{'Hypertable':<16} {'Kontrol':<22} "
            f"{'Sonuç':<6} Detay"
        )
        print(header)  # noqa: T201
        print("-" * len(header))  # noqa: T201

        for ht in HYPERTABLES:
            checks: list[tuple[str, tuple[bool, str]]] = [
                ("chunk_interval=1d", await _check_chunk_interval(conn, ht)),
                (
                    "compression_enabled",
                    await _check_compression_enabled(conn, ht),
                ),
                (
                    "compression_policy=7d",
                    await _check_compression_policy(conn, ht),
                ),
                (
                    "retention_policy=365d",
                    await _check_retention_policy(conn, ht),
                ),
            ]
            for name, (ok, detail) in checks:
                if not ok:
                    all_ok = False
                print(  # noqa: T201
                    f"{ht:<16} {name:<22} {_fmt_status(ok):<6} {detail}"
                )
            stats = await _compression_stats(conn, ht)
            print(f"{ht:<16} {'chunk_stats (info)':<22} {'INFO':<6} {stats}")  # noqa: T201

    finally:
        await conn.close()

    print()  # noqa: T201
    if all_ok:
        print("Sonuç: tüm kontroller başarılı.")  # noqa: T201
        return 0
    print("Sonuç: bir veya daha fazla kontrol başarısız.")  # noqa: T201
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
