"""Custos sağlık kontrolü.

6 kontrol çalıştırır ve exit 0 (hepsi OK) / 1 (en az bir fail) döner.

Kullanım:
    python scripts/healthcheck.py              # Human-readable
    python scripts/healthcheck.py --json       # JSON çıktı (monitoring için)

Kontroller:
    1. db_connect             — PostgreSQL asyncpg bağlantısı + SELECT 1
    2. timescaledb_extension  — pg_extension'da timescaledb aktif mi
    3. alembic_current_head   — DB migration versiyonu == son head
    4. dashboard_http         — http://localhost:8000/dashboard/overview → 200
    5. vapid_keys_present     — CUSTOS_VAPID_{PRIVATE,PUBLIC}_KEY dolu mu
    6. disk_free              — /var/custos için ≥%15 boş alan
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict

import asyncpg

from custos.shared.config import settings

DASHBOARD_URL = "http://localhost:8000/dashboard/overview"
DISK_MOUNT_POINT = "/var/custos"
DISK_MIN_FREE_PCT = 15.0
CONNECT_TIMEOUT_SEC = 5.0


class CheckResult(TypedDict):
    """Tek bir sağlık kontrolünün sonucu."""

    name: str
    status: str  # "ok" | "fail"
    detail: str


async def check_db_connect() -> CheckResult:
    """PostgreSQL'e bağlanıp SELECT 1 çalıştırır."""
    try:
        conn = await asyncio.wait_for(
            asyncpg.connect(settings.database_url_async),
            timeout=CONNECT_TIMEOUT_SEC,
        )
        try:
            value = await conn.fetchval("SELECT 1")
            if value != 1:
                return {
                    "name": "db_connect",
                    "status": "fail",
                    "detail": f"SELECT 1 beklenmedik değer: {value}",
                }
            return {"name": "db_connect", "status": "ok", "detail": "PostgreSQL erişilebilir"}
        finally:
            await conn.close()
    except Exception as exc:
        return {"name": "db_connect", "status": "fail", "detail": f"{type(exc).__name__}: {exc}"}


async def check_timescaledb_extension() -> CheckResult:
    """pg_extension'da timescaledb kaydı var mı — yoksa F11 çalışmaz."""
    try:
        conn = await asyncio.wait_for(
            asyncpg.connect(settings.database_url_async),
            timeout=CONNECT_TIMEOUT_SEC,
        )
        try:
            version = await conn.fetchval(
                "SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'",
            )
            if version:
                return {
                    "name": "timescaledb_extension",
                    "status": "ok",
                    "detail": f"TimescaleDB {version}",
                }
            return {
                "name": "timescaledb_extension",
                "status": "fail",
                "detail": "Extension yüklü değil — 'CREATE EXTENSION timescaledb' gerekli",
            }
        finally:
            await conn.close()
    except Exception as exc:
        return {
            "name": "timescaledb_extension",
            "status": "fail",
            "detail": f"{type(exc).__name__}: {exc}",
        }


async def check_alembic_current_head() -> CheckResult:
    """DB'deki migration versiyonu alembic/versions altındaki son head ile eşleşmeli."""
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        # alembic.ini proje kökünde
        alembic_ini = Path(__file__).resolve().parents[1] / "alembic.ini"
        cfg = Config(str(alembic_ini))
        script = ScriptDirectory.from_config(cfg)
        heads = script.get_heads()
        if not heads:
            return {
                "name": "alembic_current_head",
                "status": "fail",
                "detail": "Alembic heads boş — migration dosyaları yok?",
            }

        conn = await asyncio.wait_for(
            asyncpg.connect(settings.database_url_async),
            timeout=CONNECT_TIMEOUT_SEC,
        )
        try:
            # alembic_version tek satırlıktır
            current = await conn.fetchval(
                "SELECT version_num FROM alembic_version LIMIT 1",
            )
        finally:
            await conn.close()

        if current is None:
            return {
                "name": "alembic_current_head",
                "status": "fail",
                "detail": "alembic_version tablosu boş — 'alembic upgrade head' gerekli",
            }
        if current in heads:
            return {
                "name": "alembic_current_head",
                "status": "ok",
                "detail": f"Migration {current} (head)",
            }
        return {
            "name": "alembic_current_head",
            "status": "fail",
            "detail": f"Current={current}, head={heads[0]} — 'alembic upgrade head' gerekli",
        }
    except Exception as exc:
        return {
            "name": "alembic_current_head",
            "status": "fail",
            "detail": f"{type(exc).__name__}: {exc}",
        }


def check_dashboard_http() -> CheckResult:
    """Dashboard overview endpoint'ine HTTP GET — 200 mü dönüyor."""
    try:
        # urllib stdlib (aiohttp yok); timeout küçük tutulur.
        with urllib.request.urlopen(DASHBOARD_URL, timeout=CONNECT_TIMEOUT_SEC) as response:
            status = response.status
            if status == 200:
                return {
                    "name": "dashboard_http",
                    "status": "ok",
                    "detail": f"{DASHBOARD_URL} → 200 OK",
                }
            return {
                "name": "dashboard_http",
                "status": "fail",
                "detail": f"{DASHBOARD_URL} → HTTP {status}",
            }
    except urllib.error.HTTPError as exc:
        return {
            "name": "dashboard_http",
            "status": "fail",
            "detail": f"HTTP {exc.code} — {exc.reason}",
        }
    except urllib.error.URLError as exc:
        return {"name": "dashboard_http", "status": "fail", "detail": f"Bağlantı yok: {exc.reason}"}
    except Exception as exc:
        return {
            "name": "dashboard_http",
            "status": "fail",
            "detail": f"{type(exc).__name__}: {exc}",
        }


def check_vapid_keys_present() -> CheckResult:
    """VAPID private ve public anahtarları .env / Settings'ten okunabiliyor mu."""
    missing: list[str] = []
    if not settings.custos_vapid_private_key:
        missing.append("CUSTOS_VAPID_PRIVATE_KEY")
    if not settings.custos_vapid_public_key:
        missing.append("CUSTOS_VAPID_PUBLIC_KEY")
    if missing:
        return {
            "name": "vapid_keys_present",
            "status": "fail",
            "detail": f"Eksik: {', '.join(missing)} — generate_vapid_keys.py --write-env",
        }
    return {"name": "vapid_keys_present", "status": "ok", "detail": "VAPID anahtarları tanımlı"}


def check_disk_free(mount_point: str = DISK_MOUNT_POINT) -> CheckResult:
    """Veri dizini için ≥%15 boş alan gerekli."""
    try:
        usage = shutil.disk_usage(mount_point)
    except FileNotFoundError:
        return {
            "name": "disk_free",
            "status": "fail",
            "detail": f"{mount_point} mount point bulunamadı",
        }
    except Exception as exc:
        return {"name": "disk_free", "status": "fail", "detail": f"{type(exc).__name__}: {exc}"}
    percent_free = (usage.free / usage.total) * 100 if usage.total else 0.0
    free_gb = usage.free // (1024**3)
    total_gb = usage.total // (1024**3)
    detail = f"{mount_point}: %{percent_free:.1f} boş ({free_gb}/{total_gb} GB)"
    if percent_free >= DISK_MIN_FREE_PCT:
        return {"name": "disk_free", "status": "ok", "detail": detail}
    return {
        "name": "disk_free",
        "status": "fail",
        "detail": f"{detail} — minimum %{DISK_MIN_FREE_PCT:.0f} gerekli",
    }


async def run_all_checks() -> list[CheckResult]:
    """6 kontrolü sırayla çalıştırır. DB bağlantısı yoksa migration/extension de fail olur."""
    return [
        await check_db_connect(),
        await check_timescaledb_extension(),
        await check_alembic_current_head(),
        check_dashboard_http(),
        check_vapid_keys_present(),
        check_disk_free(),
    ]


def _print_human(results: list[CheckResult], overall: str) -> None:
    """İnsan okunabilir çıktı: sembol + detail + özet."""
    # Legacy kullanıcılar için: tüm kontroller OK ise "OK" satırı başta.
    if overall == "ok":
        print("OK")  # noqa: T201
    print(f"Custos healthcheck — {overall.upper()}")  # noqa: T201
    for result in results:
        symbol = "[OK]  " if result["status"] == "ok" else "[FAIL]"
        print(f"  {symbol} {result['name']}: {result['detail']}")  # noqa: T201


def _print_json(results: list[CheckResult], overall: str) -> None:
    """Monitoring tüketimi için JSON çıktı."""
    payload = {
        "status": overall,
        "checks": results,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))  # noqa: T201


async def main(output_json: bool) -> int:
    """Tüm kontrolleri çalıştır, çıktı ver, exit kodu döndür."""
    results = await run_all_checks()
    any_fail = any(r["status"] == "fail" for r in results)
    overall = "fail" if any_fail else "ok"
    if output_json:
        _print_json(results, overall)
    else:
        _print_human(results, overall)
    return 1 if any_fail else 0


def _parse_args() -> argparse.Namespace:
    """CLI argümanları."""
    parser = argparse.ArgumentParser(description="Custos sağlık kontrolü")
    parser.add_argument(
        "--json",
        action="store_true",
        help="JSON formatında çıktı (monitoring için)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    sys.exit(asyncio.run(main(output_json=args.json)))
