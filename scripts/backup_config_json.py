"""Custos config snapshot — gunluk JSON dump (V11-109 / P-06).

Cron: her gece 04:00 TRT (setup.sh /etc/cron.d/custos-backup).

Cikti: /var/custos/backup/config/config-YYYYMMDD.json (chmod 600).
Retention: 365 gun.

Kapsam:
    - tags
    - connection_profiles
    - asset_instances
    - tag_bindings
    - thresholds
    - push_subscriptions
    - maintenance_checklists, maintenance_checklist_steps
    - maintenance_schedules, maintenance_tasks, maintenance_task_step_results
    - alarm_checklist_mappings
    - retention_config (singleton)
    - users (password_hash dahil — chmod 600 bu yuzden kritik)

Sessions, audit_log, alarm_events, kpi_results, anomaly_scores, tag_readings,
service_heartbeats DAHIL DEGIL — bunlar ham telemetri/operasyonel kayitlar
olup pg_dump tarafindan haftalik yedeklenir.

DB user: custos_app yeterli (sadece SELECT). Eski tek-user kurulumlari icin
fallback ayni database_url_async uzerinden calisir.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any

import asyncpg

from custos.shared.config import settings

DEFAULT_BACKUP_DIR = Path("/var/custos/backup/config")
DEFAULT_RETENTION_DAYS = 365

# Sira onemli — restore_config_json.py asagidaki sirayla insert eder
# (FK bagimliligini gozetir).
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

VERSION = "1.0"


def _json_default(value: Any) -> Any:
    """JSON encoder fallback — asyncpg native tiplerini serilestir."""
    if isinstance(value, datetime):
        # asyncpg timezone-aware datetime dondurur; naive ise UTC kabul.
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC).isoformat()
        return value.astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time):
        return value.isoformat()
    if isinstance(value, bytes):
        # password_hash str geliyor; bytes ihtimali defansif.
        return value.decode("utf-8", errors="replace")
    if isinstance(value, set):
        return sorted(value)
    raise TypeError(f"JSON serialize edilemeyen tip: {type(value).__name__}")


async def _dump_table(conn: asyncpg.Connection, table: str) -> list[dict[str, Any]]:
    """Tabloyu dict listesine cevirir.

    Tum tablolarda ``id`` kolonu mevcut (SERIAL PK veya retention_config'te
    INTEGER PK CHECK id=1). ORDER BY id ile deterministik cikti — diff
    karsilastirmasi (--dry-run) icin onemli.
    """
    rows = await conn.fetch(f"SELECT * FROM {table} ORDER BY id")
    return [dict(row) for row in rows]


async def dump_all(dsn: str) -> dict[str, Any]:
    """Tum konfigurasyon tablolarini tek dict altinda toplar."""
    conn = await asyncpg.connect(dsn)
    try:
        tables_payload: dict[str, list[dict[str, Any]]] = {}
        for table in TABLES:
            tables_payload[table] = await _dump_table(conn, table)
        return {
            "version": VERSION,
            "created_at": datetime.now(UTC).isoformat(),
            "tables": tables_payload,
        }
    finally:
        await conn.close()


def _purge_old_backups(backup_dir: Path, retention_days: int) -> int:
    """Eski yedekleri siler (mtime > retention_days). Silinen dosya sayisini doner."""
    cutoff = datetime.now(UTC).timestamp() - (retention_days * 86400)
    removed = 0
    for entry in backup_dir.glob("config-*.json"):
        try:
            if entry.stat().st_mtime < cutoff:
                entry.unlink()
                removed += 1
        except FileNotFoundError:
            # Concurrent silme — sorun degil
            continue
    return removed


async def main(
    output_dir: Path = DEFAULT_BACKUP_DIR,
    retention_days: int = DEFAULT_RETENTION_DAYS,
) -> int:
    """Dump al, dosyaya yaz, eski yedekleri temizle. Exit kodu doner."""
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(UTC).strftime("%Y%m%d")
    out_file = output_dir / f"config-{timestamp}.json"

    payload = await dump_all(settings.database_url_async)

    # Atomik yazim — once .tmp'ye yaz, sonra rename. Yarim dosya kalmaz.
    tmp_file = out_file.with_suffix(".json.tmp")
    with tmp_file.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=_json_default)
    # chmod 600 — users.password_hash icerir, kritik dosya.
    os.chmod(tmp_file, 0o600)
    tmp_file.replace(out_file)

    total_rows = sum(len(rows) for rows in payload["tables"].values())
    removed = _purge_old_backups(output_dir, retention_days)
    size_kb = out_file.stat().st_size / 1024

    print(  # noqa: T201
        f"[backup_config_json] OK {out_file} "
        f"({total_rows} satir, {size_kb:.1f} KB, {removed} eski dosya silindi)",
    )
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Custos config JSON snapshot")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_BACKUP_DIR,
        help=f"Cikti dizini (default {DEFAULT_BACKUP_DIR})",
    )
    parser.add_argument(
        "--retention-days",
        type=int,
        default=DEFAULT_RETENTION_DAYS,
        help=f"Retention gun sayisi (default {DEFAULT_RETENTION_DAYS})",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    sys.exit(asyncio.run(main(output_dir=args.output_dir, retention_days=args.retention_days)))
