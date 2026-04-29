"""Endurance test metrik toplayıcı — 5 dakikada bir CSV satırı yazar.

Daemon olarak çalıştırılır (nohup / systemd user unit) ve `logs/endurance.csv`
dosyasına periyodik bir metrik satırı ekler. Dosya 10 MB'ı aştığında tarihli
arşiv oluşturup yeni dosya başlatır (disk dolmasını önler).

Ölçülen metrikler (her 5 dakikada bir):
    timestamp              — ISO-8601 UTC
    collector_rss_mb       — custos.critical süreç RSS (MB)
    dashboard_rss_mb       — custos.analytics süreç RSS (MB)
    db_connections         — pg_stat_activity 'custos' DB satır sayısı
    tag_readings_count     — tag_readings tablosu kayıt sayısı (cumulative)
    batch_count            — son 5 dk içinde 'Batch yazıldı' log satır sayısı
    single_fallback_count  — son log satırındaki batch_fallback (cumulative)
    tick_miss_ratio        — 1 - batch_count / beklenen  (beklenen 300 batch/5dk)
    disk_used_gb           — /opt/custos (ya da yol) kullanılan disk GB
    disk_avail_gb          — /opt/custos (ya da yol) boş disk GB

Hiçbir asyncpg/asyncio runtime akışı collector'un içine girmez — salt okur.
stdlib + opsiyonel asyncpg (DB metrikleri için) kullanılır. psutil gerekmez.

Kullanım:
    python scripts/endurance_metrics.py                 # sonsuz döngü
    python scripts/endurance_metrics.py --once          # tek ölçüm
    python scripts/endurance_metrics.py --out /tmp/e.csv --interval 60
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import csv
import io
import json
import re
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

try:  # DB metrikleri best-effort — asyncpg olmayan ortamda fail olmasın.
    import asyncpg

    _HAS_ASYNCPG = True
except ImportError:  # pragma: no cover - pyproject zorunlu kılar
    _HAS_ASYNCPG = False

# Varsayılan çalışma parametreleri
DEFAULT_INTERVAL_SEC = 300  # 5 dakika
DEFAULT_OUTPUT = Path("logs/endurance.csv")
DEFAULT_DISK_PATH = "/opt/custos"
DEFAULT_ROTATE_BYTES = 10 * 1024 * 1024  # 10 MB

# Tick hesabı — collector polling tick'i ≈ 1 s; 5 dk beklenen ≈ 300 batch.
# Gerçek collector polling preset dağılımına göre değişir; trend algılama için
# sabit bir "ideal" değer yeter (prompt kılavuzu aynı eşikleri öneriyor).
_EXPECTED_BATCH_PER_INTERVAL = 300

# journalctl "Batch yazıldı" satırını arar. JSON veya renkli konsol formatında
# olabilir. structlog JSON modunda Türkçe karakterler `\u0131 / \u015f` şeklinde
# unicode-escape ile çıktılanır (MESSAGE alanı raw string olduğu için). "Batch yaz"
# prefix'i tüm varyantları (ASCII, JSON-escaped Türkçe) tek marker ile yakalar.
_BATCH_LOG_MARKER = "Batch yaz"

# batch_fallback alanını JSON veya key=value formatında çeker.
_BATCH_FALLBACK_JSON_RE = re.compile(r'"batch_fallback"\s*:\s*(\d+)')
_BATCH_FALLBACK_KV_RE = re.compile(r"batch_fallback=(\d+)")

# V11-000-B: Collector periyodik "Tick özet" eventi yazıyor; sadece bu
# eventte `tick_miss_count` alanı görünür (marker). Üç değeri de JSON
# veya key=value biçiminden çeken regex'ler — son eventin değeri kanonik
# `tick_miss_ratio` kaynağı olur (eski "Batch yazıldı" proxy yerine).
_TICK_SUMMARY_MARKER = "tick_miss_count"
_TICK_TOTAL_JSON_RE = re.compile(r'"total_tick_count"\s*:\s*(\d+)')
_TICK_TOTAL_KV_RE = re.compile(r"total_tick_count=(\d+)")
_TICK_MISS_JSON_RE = re.compile(r'"tick_miss_count"\s*:\s*(\d+)')
_TICK_MISS_KV_RE = re.compile(r"tick_miss_count=(\d+)")
_TICK_RATIO_JSON_RE = re.compile(r'"tick_miss_ratio"\s*:\s*([\d.]+)')
_TICK_RATIO_KV_RE = re.compile(r"tick_miss_ratio=([\d.]+)")

# Systemd unit adları (default kurulum).
_COLLECTOR_UNIT = "custos-critical.service"
_DASHBOARD_UNIT = "custos.service"

# PID arama için process pattern'leri (pgrep fallback).
_COLLECTOR_PROC_PATTERN = "custos.critical"
_DASHBOARD_PROC_PATTERN = "custos.analytics"

# CSV kolonları — _write_header ve _format_row birebir eşleşir.
_CSV_COLUMNS: tuple[str, ...] = (
    "timestamp",
    "collector_rss_mb",
    "dashboard_rss_mb",
    "db_connections",
    "tag_readings_count",
    "batch_count",
    "single_fallback_count",
    "tick_miss_ratio",
    "disk_used_gb",
    "disk_avail_gb",
)


@dataclass
class EnduranceMetrics:
    """Tek bir 5-dakikalık snapshot. Tüm alanlar opsiyonel (hata → None)."""

    timestamp: datetime
    collector_rss_mb: float | None
    dashboard_rss_mb: float | None
    db_connections: int | None
    tag_readings_count: int | None
    batch_count: int | None
    single_fallback_count: int | None
    tick_miss_ratio: float | None
    disk_used_gb: float | None
    disk_avail_gb: float | None


# --- PID bulma ---


def _systemd_main_pid(unit: str) -> int | None:
    """`systemctl show --property=MainPID <unit>` — yoksa None."""
    try:
        completed = subprocess.run(
            ["systemctl", "show", "--property=MainPID", unit],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    for line in completed.stdout.splitlines():
        if line.startswith("MainPID="):
            value = line.split("=", 1)[1].strip()
            if value.isdigit() and int(value) > 0:
                return int(value)
    return None


def _pgrep_first(pattern: str) -> int | None:
    """`pgrep -f <pattern>` ilk PID'i. Bulunamazsa None."""
    try:
        completed = subprocess.run(
            ["pgrep", "-f", pattern],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    for token in completed.stdout.split():
        if token.isdigit():
            return int(token)
    return None


def find_pid(unit: str, proc_pattern: str) -> int | None:
    """Önce systemd MainPID, yoksa pgrep ile PID bul."""
    pid = _systemd_main_pid(unit)
    if pid is not None:
        return pid
    return _pgrep_first(proc_pattern)


# --- RSS okuma ---


def parse_rss_from_status(status_text: str) -> float | None:
    """/proc/<pid>/status içeriğinden VmRSS değerini MB cinsinden döndürür.

    VmRSS satırı örneği: 'VmRSS:    145236 kB'. Değer 0 ise None döner.
    """
    for line in status_text.splitlines():
        if line.startswith("VmRSS:"):
            parts = line.split()
            # ['VmRSS:', '145236', 'kB']
            if len(parts) >= 2 and parts[1].isdigit():
                kb = int(parts[1])
                return kb / 1024.0
    return None


def read_rss_mb(pid: int) -> float | None:
    """PID için /proc/<pid>/status'tan RSS (MB) okur. Süreç yoksa None."""
    status_path = Path(f"/proc/{pid}/status")
    try:
        content = status_path.read_text(encoding="utf-8")
    except (FileNotFoundError, PermissionError, OSError):
        return None
    return parse_rss_from_status(content)


# --- Disk kullanımı ---


def disk_usage_gb(mount_point: str) -> tuple[float | None, float | None]:
    """Mount point için (used_gb, avail_gb). Mount yoksa (None, None)."""
    try:
        usage = shutil.disk_usage(mount_point)
    except (FileNotFoundError, OSError):
        return (None, None)
    gb = 1024**3
    used = (usage.total - usage.free) / gb
    avail = usage.free / gb
    return (round(used, 2), round(avail, 2))


# --- Journal / log okuma ---


def parse_batch_fallback(line: str) -> int | None:
    """Bir 'Batch yazıldı' log satırından `batch_fallback` sayısını çeker.

    Destek: JSON ('\"batch_fallback\": 3') ve key=value ('batch_fallback=3').
    """
    match = _BATCH_FALLBACK_JSON_RE.search(line)
    if match is None:
        match = _BATCH_FALLBACK_KV_RE.search(line)
    if match is None:
        return None
    return int(match.group(1))


def count_batches_and_last_fallback(log_text: str) -> tuple[int, int | None]:
    """Log parçasından 'Batch yazıldı' sayısı ve son satırdaki fallback'i döndürür.

    Son satırdaki `batch_fallback` collector açılışından beri toplamdır; delta
    hesabı `endurance_daily_check.py` tarafında trend analiziyle yapılır.
    """
    batch_count = 0
    last_fallback: int | None = None
    for line in log_text.splitlines():
        if _BATCH_LOG_MARKER not in line:
            continue
        batch_count += 1
        parsed = parse_batch_fallback(line)
        if parsed is not None:
            last_fallback = parsed
    return (batch_count, last_fallback)


def fetch_recent_journal(
    unit: str,
    since: str = "5 min ago",
    timeout_sec: int = 20,
) -> str:
    """`journalctl -u <unit> --since '<since>' -o cat` çıktısını döner.

    Başarısızlıkta boş string döner — çağıran bu durumu 0 batch olarak yorumlar.
    """
    try:
        completed = subprocess.run(
            [
                "journalctl",
                "-u",
                unit,
                "--since",
                since,
                "-o",
                "cat",
                "--no-pager",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return ""
    if completed.returncode != 0:
        return ""
    return completed.stdout


def compute_tick_miss_ratio(batch_count: int, expected: int) -> float:
    """Beklenen batch'a göre eksik oranı (0.0..1.0).

    `expected` 0 ise 0.0 döner (bölme güvenliği).
    """
    if expected <= 0:
        return 0.0
    ratio = 1.0 - (batch_count / expected)
    if ratio < 0.0:
        return 0.0
    if ratio > 1.0:
        return 1.0
    return round(ratio, 4)


def extract_tick_summary_from_journal(
    log_text: str,
) -> tuple[int | None, int | None, float | None]:
    """Son "Tick özet" eventinden (total, miss, ratio) çıkarır.

    V11-000-B: Collector kendi `tick_miss_count` ve `tick_miss_ratio`
    değerlerini periyodik structlog eventiyle yazıyor. Birden fazla
    eventte son satırın değerleri kanoniktir. Hiç event yoksa ya da
    alanlar parse edilemezse ilgili dönüş öğesi None olur — çağıran
    bu durumda eski "Batch yazıldı" proxy'sine düşer.
    """
    last_total: int | None = None
    last_miss: int | None = None
    last_ratio: float | None = None
    for line in log_text.splitlines():
        if _TICK_SUMMARY_MARKER not in line:
            continue
        total_match = _TICK_TOTAL_JSON_RE.search(line) or _TICK_TOTAL_KV_RE.search(line)
        miss_match = _TICK_MISS_JSON_RE.search(line) or _TICK_MISS_KV_RE.search(line)
        ratio_match = _TICK_RATIO_JSON_RE.search(line) or _TICK_RATIO_KV_RE.search(line)
        if total_match is not None:
            last_total = int(total_match.group(1))
        if miss_match is not None:
            last_miss = int(miss_match.group(1))
        if ratio_match is not None:
            last_ratio = float(ratio_match.group(1))
    return (last_total, last_miss, last_ratio)


# --- DB metrikleri (asyncpg) ---


async def _fetch_db_metrics_async(dsn: str) -> tuple[int | None, int | None]:
    """(db_connections, tag_readings_count). Bağlantı hatası → (None, None)."""
    if not _HAS_ASYNCPG:  # pragma: no cover - runtime guard
        return (None, None)
    try:
        conn = await asyncio.wait_for(asyncpg.connect(dsn), timeout=5.0)
    except Exception:
        return (None, None)
    try:
        conns = await conn.fetchval(
            "SELECT count(*) FROM pg_stat_activity WHERE datname = 'custos'",
        )
        readings = await conn.fetchval("SELECT count(*) FROM tag_readings")
        return (
            int(conns) if conns is not None else None,
            int(readings) if readings is not None else None,
        )
    except Exception:
        return (None, None)
    finally:
        with contextlib.suppress(Exception):
            await conn.close()


def fetch_db_metrics(dsn: str | None) -> tuple[int | None, int | None]:
    """DSN yok ya da asyncpg yoksa (None, None). Aksi halde sync wrapper."""
    if not dsn or not _HAS_ASYNCPG:
        return (None, None)
    try:
        return asyncio.run(_fetch_db_metrics_async(dsn))
    except RuntimeError:
        # Beklenmedik event loop — metriği atla, daemon akmaya devam etsin.
        return (None, None)


# --- CSV yazımı + rotate ---


def _write_header_if_new(path: Path) -> None:
    """Dosya yoksa header yazar; varsa dokunmaz (yarıda durma güvenli)."""
    if path.exists() and path.stat().st_size > 0:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(_CSV_COLUMNS)


def _format_row(m: EnduranceMetrics) -> list[str]:
    """EnduranceMetrics → CSV satırı (None → boş string)."""

    def _s(v: float | int | None) -> str:
        return "" if v is None else str(v)

    return [
        m.timestamp.isoformat(),
        _s(m.collector_rss_mb),
        _s(m.dashboard_rss_mb),
        _s(m.db_connections),
        _s(m.tag_readings_count),
        _s(m.batch_count),
        _s(m.single_fallback_count),
        _s(m.tick_miss_ratio),
        _s(m.disk_used_gb),
        _s(m.disk_avail_gb),
    ]


def append_row(path: Path, metrics: EnduranceMetrics) -> None:
    """CSV'ye satır ekler; header yoksa önce yazar."""
    _write_header_if_new(path)
    with path.open("a", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(_format_row(metrics))


def rotate_if_oversized(
    path: Path,
    max_bytes: int,
    now: datetime | None = None,
) -> Path | None:
    """Dosya `max_bytes`'ı aşarsa tarihli arşive taşır.

    Dönüş: arşiv yolu (taşıma oldu) ya da None.
    """
    if not path.exists():
        return None
    size = path.stat().st_size
    if size < max_bytes:
        return None
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    archived = path.with_name(f"{path.stem}_{stamp}{path.suffix}")
    path.rename(archived)
    # Yeni dosyaya header yazılır (çağıran append_row zaten çağıracak ama
    # burada proaktif davran — dışarıdan bakıldığında dosya her zaman var).
    _write_header_if_new(path)
    return archived


# --- Tek bir ölçüm (snapshot) ---


def collect_snapshot(
    dsn: str | None,
    disk_path: str = DEFAULT_DISK_PATH,
    interval_sec: int = DEFAULT_INTERVAL_SEC,
    since_arg: str | None = None,
) -> EnduranceMetrics:
    """Tek seferlik metrik toplama — test ortamında da kullanılabilir."""
    now = datetime.now(UTC)

    # RSS
    collector_pid = find_pid(_COLLECTOR_UNIT, _COLLECTOR_PROC_PATTERN)
    dashboard_pid = find_pid(_DASHBOARD_UNIT, _DASHBOARD_PROC_PATTERN)
    collector_rss = read_rss_mb(collector_pid) if collector_pid else None
    dashboard_rss = read_rss_mb(dashboard_pid) if dashboard_pid else None

    # DB
    db_conns, tag_readings = fetch_db_metrics(dsn)

    # Journal
    since_str = since_arg or _since_from_interval(interval_sec)
    log_text = fetch_recent_journal(_COLLECTOR_UNIT, since=since_str)
    batch_count, last_fallback = count_batches_and_last_fallback(log_text)

    # V11-000-B: Önce collector'ın "Tick özet" eventinden gerçek `tick_miss_ratio`
    # okumayı dene; yoksa eski "Batch yazıldı" proxy hesabına düş (eski sürüm
    # collector veya henüz özet basılmamış pencereler için backward compat).
    _, _, summary_ratio = extract_tick_summary_from_journal(log_text)
    if summary_ratio is not None:
        tick_miss = summary_ratio
    else:
        tick_miss = compute_tick_miss_ratio(batch_count, _EXPECTED_BATCH_PER_INTERVAL)

    # Disk
    used_gb, avail_gb = disk_usage_gb(disk_path)

    return EnduranceMetrics(
        timestamp=now,
        collector_rss_mb=(round(collector_rss, 2) if collector_rss else None),
        dashboard_rss_mb=(round(dashboard_rss, 2) if dashboard_rss else None),
        db_connections=db_conns,
        tag_readings_count=tag_readings,
        batch_count=batch_count,
        single_fallback_count=last_fallback,
        tick_miss_ratio=tick_miss,
        disk_used_gb=used_gb,
        disk_avail_gb=avail_gb,
    )


def _since_from_interval(interval_sec: int) -> str:
    """journalctl --since için '<N>s ago' stringi üretir."""
    return f"{max(1, int(interval_sec))}s ago"


# --- Ana daemon döngüsü ---


class _ShutdownFlag:
    """SIGTERM/SIGINT için thread-safe bayrak (daemon graceful kapanışı)."""

    def __init__(self) -> None:
        self._stop = False

    def trigger(self, *_args: object) -> None:
        self._stop = True

    @property
    def stopped(self) -> bool:
        return self._stop


def _install_signal_handlers(flag: _ShutdownFlag) -> None:
    """SIGINT + SIGTERM → flag trigger. Windows'ta SIGTERM yoksa sessizce geç."""
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, flag.trigger)
        except (ValueError, AttributeError):  # pragma: no cover - platform guard
            pass


def _sleep_interruptible(interval_sec: int, flag: _ShutdownFlag) -> None:
    """Çeyrek saniyelik adımlarla uyur; SIGTERM geldiğinde erken çıkar."""
    end = time.monotonic() + interval_sec
    while not flag.stopped:
        remaining = end - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(0.25, remaining))


def run_forever(
    output: Path,
    interval_sec: int,
    disk_path: str,
    max_bytes: int,
    dsn: str | None,
) -> int:
    """Sonsuz döngü — SIGTERM/SIGINT ile graceful dur. Her tick 1 satır yazar."""
    flag = _ShutdownFlag()
    _install_signal_handlers(flag)
    _write_header_if_new(output)

    while not flag.stopped:
        snapshot = collect_snapshot(
            dsn=dsn,
            disk_path=disk_path,
            interval_sec=interval_sec,
        )
        append_row(output, snapshot)
        rotate_if_oversized(output, max_bytes)
        _sleep_interruptible(interval_sec, flag)

    return 0


# --- CLI ---


def _resolve_dsn(arg: str | None) -> str | None:
    """CLI --dsn > settings.database_url_async > None."""
    if arg:
        return arg
    try:
        from custos.shared.config import settings

        return str(settings.database_url_async)
    except Exception:
        return None


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Endurance test metrik toplayıcı (5 dakikada bir CSV)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Çıktı CSV yolu (varsayılan: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=DEFAULT_INTERVAL_SEC,
        help=f"Ölçüm aralığı saniye (varsayılan: {DEFAULT_INTERVAL_SEC} = 5 dk)",
    )
    parser.add_argument(
        "--disk-path",
        default=DEFAULT_DISK_PATH,
        help=f"Disk kullanımı için mount (varsayılan: {DEFAULT_DISK_PATH})",
    )
    parser.add_argument(
        "--max-bytes",
        type=int,
        default=DEFAULT_ROTATE_BYTES,
        help="CSV rotate eşiği byte (varsayılan: 10 MB)",
    )
    parser.add_argument(
        "--dsn",
        default=None,
        help="PostgreSQL DSN; boşsa settings.database_url_async kullanılır",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Tek bir ölçüm al ve çık (test/cron modu)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. --once ise tek ölçüm, değilse daemon."""
    args = _parse_args(argv)
    dsn = _resolve_dsn(args.dsn)

    if args.once:
        snapshot = collect_snapshot(
            dsn=dsn,
            disk_path=args.disk_path,
            interval_sec=args.interval,
        )
        append_row(args.out, snapshot)
        rotate_if_oversized(args.out, args.max_bytes)
        # Tek satır özet (insan okur).
        print(_one_line_summary(snapshot))  # noqa: T201 — CLI
        return 0

    return run_forever(
        output=args.out,
        interval_sec=args.interval,
        disk_path=args.disk_path,
        max_bytes=args.max_bytes,
        dsn=dsn,
    )


def _one_line_summary(m: EnduranceMetrics) -> str:
    """Konsol için kısa özet (--once modu)."""
    buf = io.StringIO()
    buf.write(f"[{m.timestamp.isoformat()}] ")
    buf.write(f"collector_rss={m.collector_rss_mb} MB ")
    buf.write(f"dashboard_rss={m.dashboard_rss_mb} MB ")
    buf.write(f"db_conns={m.db_connections} ")
    buf.write(f"readings={m.tag_readings_count} ")
    buf.write(f"batches={m.batch_count} ")
    buf.write(f"fallback={m.single_fallback_count} ")
    buf.write(f"tick_miss={m.tick_miss_ratio} ")
    buf.write(f"disk_used={m.disk_used_gb} GB / avail={m.disk_avail_gb} GB")
    return buf.getvalue()


# JSON yardımcı export — test / daily_check için.
def metrics_to_json(m: EnduranceMetrics) -> str:
    """EnduranceMetrics → JSON string (rapor ve test kullanımı)."""
    payload = {
        "timestamp": m.timestamp.isoformat(),
        "collector_rss_mb": m.collector_rss_mb,
        "dashboard_rss_mb": m.dashboard_rss_mb,
        "db_connections": m.db_connections,
        "tag_readings_count": m.tag_readings_count,
        "batch_count": m.batch_count,
        "single_fallback_count": m.single_fallback_count,
        "tick_miss_ratio": m.tick_miss_ratio,
        "disk_used_gb": m.disk_used_gb,
        "disk_avail_gb": m.disk_avail_gb,
    }
    return json.dumps(payload, ensure_ascii=False)


if __name__ == "__main__":
    sys.exit(main())
