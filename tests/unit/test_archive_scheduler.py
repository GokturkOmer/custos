"""Archive scheduler zaman hesabı unit testleri.

Scheduler'ın cron benzeri davranışı saf zaman hesabında olduğundan DB'siz
test edilebilir: ``_compute_next_run_utc`` her zaman bir sonraki "ayın 1'i
02:00 TRT"yi UTC olarak döndürmeli.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from custos.analytics.archive_scheduler import _compute_next_run_utc
from custos.analytics.archiver import _month_bounds_utc, _previous_month

_TRT = ZoneInfo("Europe/Istanbul")


def test_next_run_within_same_month_before_trigger() -> None:
    """Ayın 1'i 01:00 TRT → aynı ayın 1'i 02:00 TRT döner."""
    ref_local = datetime(2026, 5, 1, 1, 0, 0, tzinfo=_TRT)
    result = _compute_next_run_utc(ref_local.astimezone(UTC))
    expected = datetime(2026, 5, 1, 2, 0, 0, tzinfo=_TRT).astimezone(UTC)
    assert result == expected


def test_next_run_rolls_to_next_month_after_trigger() -> None:
    """Ayın 5'i → bir sonraki ayın 1'i 02:00 TRT döner."""
    ref_local = datetime(2026, 5, 5, 12, 0, 0, tzinfo=_TRT)
    result = _compute_next_run_utc(ref_local.astimezone(UTC))
    expected = datetime(2026, 6, 1, 2, 0, 0, tzinfo=_TRT).astimezone(UTC)
    assert result == expected


def test_next_run_rolls_over_year_boundary() -> None:
    """Aralık sonu → bir sonraki ocak 1'i 02:00 TRT döner."""
    ref_local = datetime(2026, 12, 15, 12, 0, 0, tzinfo=_TRT)
    result = _compute_next_run_utc(ref_local.astimezone(UTC))
    expected = datetime(2027, 1, 1, 2, 0, 0, tzinfo=_TRT).astimezone(UTC)
    assert result == expected


def test_month_bounds_utc_december_rolls_year() -> None:
    """Aralık ayı için sınırlar: start=Ara 1, end=Oca 1 (bir sonraki yıl)."""
    start, end = _month_bounds_utc(2026, 12)
    assert start == datetime(2026, 12, 1, tzinfo=_TRT).astimezone(UTC)
    assert end == datetime(2027, 1, 1, tzinfo=_TRT).astimezone(UTC)


def test_month_bounds_utc_rejects_invalid_month() -> None:
    """Ay 1..12 dışında ValueError fırlatır."""
    with pytest.raises(ValueError):
        _month_bounds_utc(2026, 0)
    with pytest.raises(ValueError):
        _month_bounds_utc(2026, 13)


def test_previous_month_within_same_year() -> None:
    """Mayıs referansı → Nisan döner."""
    ref = datetime(2026, 5, 10, 3, 0, 0, tzinfo=_TRT).astimezone(UTC)
    assert _previous_month(ref) == (2026, 4)


def test_previous_month_rolls_year_backwards() -> None:
    """Ocak 1 referansı → bir önceki aralık döner."""
    ref = datetime(2026, 1, 1, 2, 0, 0, tzinfo=_TRT).astimezone(UTC)
    assert _previous_month(ref) == (2025, 12)
