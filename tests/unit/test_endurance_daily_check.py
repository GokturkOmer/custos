"""Endurance günlük kontrol — CSV'yi sentezleyip kırmızı bayrak mantığını
 doğrulayan unit testler.

3 senaryo (prompt): normal / memory leak / tick miss artışı. Ek olarak linear
slope, percent_change, CSV window filtresi ve boş CSV tolerance testleri.
Hiçbir testin gerçek DB / journalctl / dosya sistemine ihtiyacı yoktur.
"""

from __future__ import annotations

import csv
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scripts.endurance_daily_check import (
    Status,
    build_report,
    check_db_connections,
    check_disk,
    check_readings_rate,
    column_series,
    filter_window,
    linear_slope,
    percent_change,
    read_csv_rows,
)
from scripts.endurance_metrics import _CSV_COLUMNS

# --- Yardımcılar: senaryo CSV sentezle ---


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    """Verilen satır sözlüklerini gerçek CSV kolon sırasıyla yazar."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(_CSV_COLUMNS))
        writer.writeheader()
        for row in rows:
            writer.writerow({k: ("" if row.get(k) is None else row[k]) for k in _CSV_COLUMNS})


def _row(
    ts: datetime,
    *,
    collector_rss: float | None = 145.0,
    dashboard_rss: float | None = 180.0,
    db_conns: int | None = 8,
    tag_readings: int | None = 10000,
    batch: int | None = 300,
    fallback: int | None = 3,
    tick_miss: float | None = 0.0,
    disk_used: float | None = 2.0,
    disk_avail: float | None = 48.0,
) -> dict[str, object]:
    """Default değerlerle (tüm kontroller OK) bir CSV satırı."""
    return {
        "timestamp": ts.isoformat(),
        "collector_rss_mb": collector_rss,
        "dashboard_rss_mb": dashboard_rss,
        "db_connections": db_conns,
        "tag_readings_count": tag_readings,
        "batch_count": batch,
        "single_fallback_count": fallback,
        "tick_miss_ratio": tick_miss,
        "disk_used_gb": disk_used,
        "disk_avail_gb": disk_avail,
    }


def _normal_series(start: datetime, hours: int = 24) -> list[dict[str, object]]:
    """24 saatte flat memory + düzenli readings — tamamen OK senaryo."""
    rows: list[dict[str, object]] = []
    for h in range(hours):
        ts = start + timedelta(hours=h)
        rows.append(
            _row(
                ts,
                collector_rss=145.0 + (h * 0.05),  # mikro dalga (~%1'den az)
                dashboard_rss=180.0 + (h * 0.05),
                tag_readings=10000 + h * 3600,  # saatte 3600 okuma (=1/s)
                tick_miss=0.002,
            ),
        )
    return rows


# --- Saf yardımcı testler ---


def test_percent_change_basic() -> None:
    assert percent_change(100.0, 105.0) == pytest.approx(5.0)
    assert percent_change(100.0, 90.0) == pytest.approx(-10.0)
    assert percent_change(0.0, 50.0) == 0.0  # bölme güvenliği


def test_linear_slope_increasing() -> None:
    """y = 2x: slope 2 olmalı (saatler zaman ekseninde)."""
    t0 = datetime(2026, 4, 22, 0, 0, 0, tzinfo=UTC)
    series = [(t0 + timedelta(hours=h), float(2 * h)) for h in range(5)]
    assert linear_slope(series) == pytest.approx(2.0)


def test_linear_slope_flat_is_zero() -> None:
    t0 = datetime(2026, 4, 22, 0, 0, 0, tzinfo=UTC)
    series = [(t0 + timedelta(hours=h), 100.0) for h in range(5)]
    assert linear_slope(series) == pytest.approx(0.0)


def test_linear_slope_single_point_returns_zero() -> None:
    t0 = datetime(2026, 4, 22, tzinfo=UTC)
    assert linear_slope([(t0, 5.0)]) == 0.0


def test_filter_window_respects_window(tmp_path: Path) -> None:
    t0 = datetime(2026, 4, 22, 12, 0, 0, tzinfo=UTC)
    rows = [{"timestamp": (t0 + timedelta(hours=h)).isoformat()} for h in range(10)]
    # 5 saatlik pencere → son 6 satır (t=5..t=10-1 arası end'e kadar)
    end = t0 + timedelta(hours=9)
    filtered = filter_window(rows, end=end, window=timedelta(hours=5))
    assert len(filtered) == 6  # hours 4..9 dahil
    first_ts = datetime.fromisoformat(filtered[0]["timestamp"])
    assert first_ts == t0 + timedelta(hours=4)


def test_column_series_skips_none_rows() -> None:
    rows = [
        {"timestamp": "2026-04-22T00:00:00+00:00", "collector_rss_mb": "145.0"},
        {"timestamp": "2026-04-22T01:00:00+00:00", "collector_rss_mb": ""},
        {"timestamp": "2026-04-22T02:00:00+00:00", "collector_rss_mb": "146.5"},
    ]
    series = column_series(rows, "collector_rss_mb")
    assert len(series) == 2
    assert series[1][1] == 146.5


# --- Senaryo 1: normal (hepsi OK) ---


def test_scenario_normal_overall_ok(tmp_path: Path) -> None:
    """24 saat flat veri — overall OK, exit 0."""
    csv_path = tmp_path / "endurance.csv"
    start = datetime(2026, 4, 22, 0, 0, 0, tzinfo=UTC)
    _write_csv(csv_path, _normal_series(start, hours=24))
    now = start + timedelta(hours=23, minutes=30)

    report = build_report(csv_path, now=now)
    assert report.overall == Status.OK
    # Tüm memory + disk + tick miss + db kontrolleri OK
    memory_checks = [c for c in report.checks if "RSS" in c.name]
    assert all(c.status == Status.OK for c in memory_checks)


# --- Senaryo 2: memory leak (RSS %12 artış) ---


def test_scenario_memory_leak_overall_crit(tmp_path: Path) -> None:
    """Collector RSS 24 saatte %12 artış → CRIT, exit 2."""
    csv_path = tmp_path / "endurance.csv"
    start = datetime(2026, 4, 22, 0, 0, 0, tzinfo=UTC)
    rows = _normal_series(start, hours=24)
    # Collector RSS'i lineer artır: 145 → 162 (+%11.7)
    for h, row in enumerate(rows):
        row["collector_rss_mb"] = 145.0 + h * 0.75
    _write_csv(csv_path, rows)
    now = start + timedelta(hours=23, minutes=30)

    report = build_report(csv_path, now=now)
    assert report.overall == Status.CRIT
    collector = next(c for c in report.checks if c.name == "Collector RSS")
    assert collector.status == Status.CRIT


# --- Senaryo 3: tick miss artışı (0.06 ortalama + yukarı slope) ---


def test_scenario_tick_miss_escalation_overall_crit(tmp_path: Path) -> None:
    """Tick miss 0.06 ortalama + yukarı trend → CRIT."""
    csv_path = tmp_path / "endurance.csv"
    start = datetime(2026, 4, 22, 0, 0, 0, tzinfo=UTC)
    rows = _normal_series(start, hours=24)
    # 0.01'den 0.12'ye lineer artan tick miss
    for h, row in enumerate(rows):
        row["tick_miss_ratio"] = 0.01 + h * 0.005
    _write_csv(csv_path, rows)
    now = start + timedelta(hours=23, minutes=30)

    report = build_report(csv_path, now=now)
    assert report.overall == Status.CRIT
    tick = next(c for c in report.checks if c.name == "Tick miss oranı")
    assert tick.status == Status.CRIT


# --- Kontroller — izole senaryolar ---


def test_check_db_connections_pool_exhaustion() -> None:
    """DB connections max >= 90 → CRIT."""
    t0 = datetime(2026, 4, 22, tzinfo=UTC)
    rows = [
        {"timestamp": (t0 + timedelta(hours=h)).isoformat(), "db_connections": str(85 + h)}
        for h in range(6)
    ]
    check = check_db_connections(rows)
    assert check.status == Status.CRIT


def test_check_disk_crit_when_avail_below_15pct() -> None:
    """Boş disk %15 altındaysa CRIT."""
    t0 = datetime(2026, 4, 22, tzinfo=UTC)
    rows = [
        {
            "timestamp": (t0 + timedelta(hours=h)).isoformat(),
            "disk_used_gb": str(90 + h * 0.1),
            "disk_avail_gb": str(10 - h * 0.05),  # 10 GB'dan 9.75 GB'a düşüyor
        }
        for h in range(6)
    ]
    check = check_disk(rows)
    assert check.status == Status.CRIT


def test_check_readings_rate_drop_triggers_warn() -> None:
    """İkinci yarı okuma hızı %25 düşük → WARN."""
    t0 = datetime(2026, 4, 22, tzinfo=UTC)
    rows = []
    cumulative = 0.0
    # İlk 12 saat: 3600/saat
    for h in range(12):
        rows.append(
            {
                "timestamp": (t0 + timedelta(hours=h)).isoformat(),
                "tag_readings_count": str(int(cumulative)),
            },
        )
        cumulative += 3600
    # Son 12 saat: 2400/saat (~%33 düşüş)
    for h in range(12, 24):
        rows.append(
            {
                "timestamp": (t0 + timedelta(hours=h)).isoformat(),
                "tag_readings_count": str(int(cumulative)),
            },
        )
        cumulative += 2400
    check = check_readings_rate(rows)
    assert check.status == Status.WARN


def test_empty_csv_window_reports_warn(tmp_path: Path) -> None:
    """Son 24 saatte hiç satır yoksa WARN (daemon başlamamış olabilir)."""
    csv_path = tmp_path / "endurance.csv"
    _write_csv(csv_path, [])
    report = build_report(csv_path, now=datetime(2026, 4, 22, tzinfo=UTC))
    assert report.overall == Status.WARN


def test_read_csv_rows_returns_ordered_rows(tmp_path: Path) -> None:
    """CSV okuyucu header + dictleri sırasıyla döner."""
    csv_path = tmp_path / "endurance.csv"
    start = datetime(2026, 4, 22, 0, 0, 0, tzinfo=UTC)
    _write_csv(csv_path, _normal_series(start, hours=3))
    rows = read_csv_rows(csv_path)
    assert len(rows) == 3
    assert rows[0]["timestamp"].startswith("2026-04-22T00:00")
