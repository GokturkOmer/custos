"""Custos config JSON restore — Developer manuel kullanir (V11-109 / P-06).

Kullanim:
    # Dry-run — JSON ile DB arasindaki sayisal fark (yazma yok)
    python scripts/restore_config_json.py --file backup.json --dry-run

    # Apply — ID-bazli ON CONFLICT UPDATE (DEGISTIRIR)
    python scripts/restore_config_json.py --file backup.json --apply

Strateji: ID-bazli soft UPSERT
    - DB'de ayni id varsa UPDATE (kolonlar EXCLUDED'dan).
    - Yoksa INSERT.
    - JSON'da olmayip DB'de olan satirlar SILINMEZ — restore softdir.
    - SERIAL sequence yedek sonrasi MAX(id) + 1'e cekilir (yeni INSERT'te
      id collision olmasin).

DB user: ``custos_admin`` gerekli (sequence setval + UPDATE/INSERT). Eski
tek-user kurulumlarinda fallback ``database_url_async`` uzerinden calisir.

Restore wizard pilot kurulum oncesi staging DB'de test edilir
(``docs/v1_1_plan.md`` V11-109 deliverable maddesi).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

import asyncpg

from custos.shared.config import settings

# backup_config_json.py ile AYNI sira — FK bagimliligini gozeten INSERT akisi.
TABLES: tuple[str, ...] = (
    "retention_config",
    "users",
    "connection_profiles",
    "tags",
    "asset_instances",
    "tag_bindings",
    "thresholds",
    "push_subscriptions",
    "maintenance_checklists",
    "maintenance_checklist_steps",
    "maintenance_schedules",
    "maintenance_tasks",
    "maintenance_task_step_results",
    "alarm_checklist_mappings",
)


def _parse_value(value: Any, data_type: str) -> Any:
    """JSON native tipini DB tipine cevirir.

    JSON'da datetime/date/time string olarak yer alir; asyncpg insert
    icin Python objelerine cevirilmesi gerekli. Diger tipler (int, str,
    bool, float, dict, list) JSON'dan zaten dogru tipte gelir.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        return value
    if data_type in ("timestamp with time zone", "timestamp without time zone"):
        return datetime.fromisoformat(value)
    if data_type == "date":
        return date.fromisoformat(value)
    if data_type in ("time without time zone", "time with time zone"):
        return time.fromisoformat(value)
    return value


async def _get_table_columns(
    conn: asyncpg.Connection,
    table: str,
) -> list[tuple[str, str]]:
    """Tablonun (column_name, data_type) listesini ordinal_position sirayla doner."""
    rows = await conn.fetch(
        """
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = $1
        ORDER BY ordinal_position
        """,
        table,
    )
    return [(r["column_name"], r["data_type"]) for r in rows]


async def _diff_table(
    conn: asyncpg.Connection,
    table: str,
    json_rows: list[dict[str, Any]],
) -> dict[str, int]:
    """JSON ile DB arasindaki sayisal fark (insert/update/delete)."""
    db_rows = await conn.fetch(f"SELECT id FROM {table}")
    db_ids = {row["id"] for row in db_rows}
    json_ids = {row["id"] for row in json_rows}

    to_insert = json_ids - db_ids
    to_update = json_ids & db_ids
    to_delete = db_ids - json_ids
    return {
        "insert": len(to_insert),
        "update": len(to_update),
        "delete": len(to_delete),
        "json_total": len(json_ids),
        "db_total": len(db_ids),
    }


async def diff_all(payload: dict[str, Any]) -> dict[str, dict[str, int]]:
    """Tum tablolar icin diff hesapla (dry-run)."""
    conn = await asyncpg.connect(settings.database_admin_url)
    try:
        result: dict[str, dict[str, int]] = {}
        for table in TABLES:
            json_rows = payload["tables"].get(table, [])
            result[table] = await _diff_table(conn, table, json_rows)
        return result
    finally:
        await conn.close()


async def _upsert_table(
    conn: asyncpg.Connection,
    table: str,
    json_rows: list[dict[str, Any]],
) -> int:
    """ID-bazli UPSERT — INSERT ON CONFLICT (id) DO UPDATE."""
    if not json_rows:
        return 0

    columns = await _get_table_columns(conn, table)
    column_names = [c[0] for c in columns]
    type_map = dict(columns)

    placeholders = ",".join(f"${i + 1}" for i in range(len(column_names)))
    update_clause = ",".join(
        f"{col}=EXCLUDED.{col}" for col in column_names if col != "id"
    )
    sql = (
        f"INSERT INTO {table} ({','.join(column_names)}) "
        f"VALUES ({placeholders}) "
        f"ON CONFLICT (id) DO UPDATE SET {update_clause}"
    )

    affected = 0
    async with conn.transaction():
        for row in json_rows:
            values = [
                _parse_value(row.get(col), type_map[col]) for col in column_names
            ]
            await conn.execute(sql, *values)
            affected += 1
        # SERIAL sequence'i MAX(id)'e set et — yeni INSERT'lerde id collision olmasin.
        # retention_config singleton, SERIAL degil — atlanir.
        if table != "retention_config":
            try:
                await conn.fetchval(
                    f"""
                    SELECT setval(
                        pg_get_serial_sequence('{table}', 'id'),
                        COALESCE((SELECT MAX(id) FROM {table}), 1)
                    )
                    """,
                )
            except asyncpg.PostgresError:
                # Defansif — non-SERIAL PK'ler icin
                pass
    return affected


async def apply_all(payload: dict[str, Any]) -> dict[str, int]:
    """Tum tablolari ID-bazli upsert eder. Tablo basina etkilenen satir."""
    conn = await asyncpg.connect(settings.database_admin_url)
    try:
        result: dict[str, int] = {}
        for table in TABLES:
            json_rows = payload["tables"].get(table, [])
            result[table] = await _upsert_table(conn, table, json_rows)
        return result
    finally:
        await conn.close()


def _print_diff(diff: dict[str, dict[str, int]]) -> None:
    """Dry-run ozet tablosunu basar."""
    header = (
        f"{'Tablo':<35} {'JSON':>7} {'DB':>7} "
        f"{'Insert':>7} {'Update':>7} {'Delete*':>7}"
    )
    print("[restore_config_json] DRY-RUN diff (yazma yapilmadi):\n")  # noqa: T201
    print(header)  # noqa: T201
    print("-" * len(header))  # noqa: T201
    total_insert = total_update = total_delete = 0
    for table, stats in diff.items():
        print(  # noqa: T201
            f"{table:<35} {stats['json_total']:>7} {stats['db_total']:>7} "
            f"{stats['insert']:>7} {stats['update']:>7} {stats['delete']:>7}",
        )
        total_insert += stats["insert"]
        total_update += stats["update"]
        total_delete += stats["delete"]
    print("-" * len(header))  # noqa: T201
    print(  # noqa: T201
        f"{'TOPLAM':<35} {'':>7} {'':>7} "
        f"{total_insert:>7} {total_update:>7} {total_delete:>7}",
    )
    print(  # noqa: T201
        "\n* Delete sutunu: DB'de var, JSON'da yok. Restore softdir, "
        "bu satirlari SILMEZ.",
    )


def _print_apply(result: dict[str, int]) -> None:
    """Apply ozet tablosunu basar."""
    print("[restore_config_json] APPLY tamamlandi:\n")  # noqa: T201
    total = 0
    for table, count in result.items():
        print(f"  {table:<35} {count:>5} satir upsert edildi")  # noqa: T201
        total += count
    print(f"\n  TOPLAM: {total} satir")  # noqa: T201


async def _main(file_path: Path, mode: str) -> int:
    """Dosyayi yukle, mod'a gore diff veya apply calistir."""
    if not file_path.exists():
        print(f"HATA: Dosya bulunamadi: {file_path}", file=sys.stderr)  # noqa: T201
        return 1
    with file_path.open("r", encoding="utf-8") as f:
        payload = json.load(f)

    if "version" not in payload or "tables" not in payload:
        print(  # noqa: T201
            "HATA: Gecersiz JSON yapisi — 'version' veya 'tables' alani eksik.",
            file=sys.stderr,
        )
        return 1

    if mode == "dry-run":
        diff = await diff_all(payload)
        _print_diff(diff)
    else:
        result = await apply_all(payload)
        _print_apply(result)
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Custos config JSON restore (Developer manuel)",
    )
    parser.add_argument(
        "--file",
        type=Path,
        required=True,
        help="JSON yedek dosyasi (backup_config_json.py ciktisi)",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--dry-run",
        action="store_true",
        help="DB ile JSON arasindaki fark — yazma yapmaz",
    )
    group.add_argument(
        "--apply",
        action="store_true",
        help="ID-bazli upsert (ON CONFLICT UPDATE) — DB'YI DEGISTIRIR",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    chosen_mode = "dry-run" if args.dry_run else "apply"
    sys.exit(asyncio.run(_main(args.file, chosen_mode)))
