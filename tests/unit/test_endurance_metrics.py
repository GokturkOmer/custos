"""Endurance metrics toplayıcı için unit testler.

DB / journalctl çağrıları mock'lanır — saf parse + CSV rotate + tick oranı
doğrulaması. Amaç: metrik toplayıcı 7 gün boyunca sessizce çalıştığında
kırılabilecek kenar durumları erken yakalamak.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from scripts.endurance_metrics import (
    _CSV_COLUMNS,
    EnduranceMetrics,
    _format_row,
    _write_header_if_new,
    append_row,
    compute_tick_miss_ratio,
    count_batches_and_last_fallback,
    parse_batch_fallback,
    parse_rss_from_status,
    rotate_if_oversized,
)

# --- RSS parse ---


def test_parse_rss_extracts_vmrss_in_mb() -> None:
    """VmRSS satırı doğru parse edilip MB cinsinden döner."""
    status = (
        "Name:\tpython\n"
        "State:\tS (sleeping)\n"
        "VmPeak:\t  200000 kB\n"
        "VmSize:\t  180000 kB\n"
        "VmRSS:\t  145408 kB\n"  # 145408 / 1024 = 142.0
        "VmData:\t   50000 kB\n"
    )
    result = parse_rss_from_status(status)
    assert result is not None
    assert result == pytest.approx(142.0)


def test_parse_rss_returns_none_if_missing() -> None:
    """VmRSS satırı yoksa None döner (hata fırlatmaz)."""
    assert parse_rss_from_status("Name:\tpython\nState:\tS\n") is None


# --- Batch fallback parse ---


def test_parse_batch_fallback_json_format() -> None:
    """structlog JSON satırından batch_fallback doğru çekilir."""
    line = (
        '{"event": "Batch yazıldı", "toplam": 42, "batch_okuma": 100, '
        '"batch_fallback": 7, "tekil_okuma": 3}'
    )
    assert parse_batch_fallback(line) == 7


def test_parse_batch_fallback_kv_format() -> None:
    """ConsoleRenderer key=value satırından batch_fallback çekilir."""
    line = "2026-04-22 event=Batch yazıldı batch_okuma=100 batch_fallback=12"
    assert parse_batch_fallback(line) == 12


def test_parse_batch_fallback_missing_returns_none() -> None:
    """Alan yoksa None (üzerine yazılmaz)."""
    assert parse_batch_fallback("Batch yazıldı toplam=5") is None


def test_count_batches_sums_marker_and_keeps_last_fallback() -> None:
    """Log parçasında batch sayısı + son batch_fallback doğru döner."""
    log = (
        '{"event": "Batch yazıldı", "batch_fallback": 1}\n'
        "  (ara bir log) info=ignore\n"
        '{"event": "Batch yazıldı", "batch_fallback": 2}\n'
        '{"event": "Batch yazıldı", "batch_fallback": 5}\n'
    )
    count, last = count_batches_and_last_fallback(log)
    assert count == 3
    assert last == 5


def test_count_batches_empty_log_returns_zero_and_none() -> None:
    """Log boşsa (0, None). Tick miss hesabı bu durumda 1.0 olur."""
    assert count_batches_and_last_fallback("") == (0, None)


# --- Tick miss oranı ---


def test_compute_tick_miss_ratio_full_match_is_zero() -> None:
    """Beklenen = actual → 0.0."""
    assert compute_tick_miss_ratio(300, 300) == 0.0


def test_compute_tick_miss_ratio_half_match() -> None:
    """Yarı batch → 0.5."""
    assert compute_tick_miss_ratio(150, 300) == 0.5


def test_compute_tick_miss_ratio_zero_batches_is_one() -> None:
    """0 batch → 1.0 (tamamı kayıp)."""
    assert compute_tick_miss_ratio(0, 300) == 1.0


def test_compute_tick_miss_ratio_overshoot_clamped_to_zero() -> None:
    """Aktüel beklenenden fazlaysa (hızlı tick) 0.0'a clamp."""
    assert compute_tick_miss_ratio(400, 300) == 0.0


def test_compute_tick_miss_ratio_zero_expected_returns_zero() -> None:
    """Expected=0 → divide-by-zero yerine 0.0 (güvenli default)."""
    assert compute_tick_miss_ratio(50, 0) == 0.0


# --- CSV rotate ---


def test_rotate_when_exceeds_threshold(tmp_path: Path) -> None:
    """Dosya eşiği aşınca tarihli arşive taşınır, yeni boş dosya başlar."""
    csv_path = tmp_path / "endurance.csv"
    csv_path.write_bytes(b"x" * 2048)  # 2 KB
    archived = rotate_if_oversized(csv_path, max_bytes=1024)
    assert archived is not None
    assert archived.exists()
    assert archived.stat().st_size == 2048
    # Yeni dosya header ile doğdu (boş değil)
    assert csv_path.exists()
    header = csv_path.read_text(encoding="utf-8").splitlines()[0].split(",")
    assert header == list(_CSV_COLUMNS)


def test_rotate_below_threshold_noop(tmp_path: Path) -> None:
    """Dosya eşik altındaysa rotate olmaz (None döner)."""
    csv_path = tmp_path / "endurance.csv"
    csv_path.write_bytes(b"x" * 512)
    archived = rotate_if_oversized(csv_path, max_bytes=1024)
    assert archived is None
    assert csv_path.stat().st_size == 512


def test_rotate_missing_file_returns_none(tmp_path: Path) -> None:
    """Dosya yoksa None (ilk çalıştırma senaryosu)."""
    csv_path = tmp_path / "does_not_exist.csv"
    assert rotate_if_oversized(csv_path, max_bytes=1024) is None


# --- Append row + header ---


def test_write_header_and_append_row(tmp_path: Path) -> None:
    """append_row: dosya yoksa header'ı ekleyip satır yazar."""
    csv_path = tmp_path / "e.csv"
    _write_header_if_new(csv_path)
    metrics = EnduranceMetrics(
        timestamp=datetime(2026, 4, 22, 18, 0, 0, tzinfo=UTC),
        collector_rss_mb=145.2,
        dashboard_rss_mb=180.5,
        db_connections=8,
        tag_readings_count=12500,
        batch_count=300,
        single_fallback_count=3,
        tick_miss_ratio=0.0,
        disk_used_gb=2.1,
        disk_avail_gb=47.9,
    )
    append_row(csv_path, metrics)

    lines = csv_path.read_text(encoding="utf-8").splitlines()
    assert lines[0].split(",") == list(_CSV_COLUMNS)
    assert lines[1].startswith("2026-04-22T18:00:00")
    # None'suz happy path — tüm kolonlar dolu
    assert lines[1].split(",")[-1] == "47.9"


def test_format_row_none_becomes_empty_string() -> None:
    """Eksik metrik None → boş CSV hücresi (NULL semantik)."""
    metrics = EnduranceMetrics(
        timestamp=datetime(2026, 4, 22, 18, 0, 0, tzinfo=UTC),
        collector_rss_mb=None,
        dashboard_rss_mb=None,
        db_connections=None,
        tag_readings_count=None,
        batch_count=0,
        single_fallback_count=None,
        tick_miss_ratio=1.0,
        disk_used_gb=None,
        disk_avail_gb=None,
    )
    row = _format_row(metrics)
    assert row[1] == ""  # collector_rss_mb
    assert row[5] == "0"  # batch_count
    assert row[7] == "1.0"  # tick_miss_ratio
