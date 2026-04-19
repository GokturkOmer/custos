"""compute_next_due_at unit testleri — pure fonksiyon, DB gerektirmez."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from custos.shared.maintenance_periods import compute_next_due_at


def test_daily_default_value() -> None:
    """daily + value=1 → 1 gün ileri."""
    current = datetime(2026, 5, 1, 9, 0, tzinfo=UTC)
    result = compute_next_due_at(current, "daily", 1)
    assert result == datetime(2026, 5, 2, 9, 0, tzinfo=UTC)


def test_daily_multiplier() -> None:
    """daily + value=3 → 3 gün ileri."""
    current = datetime(2026, 5, 1, 9, 0, tzinfo=UTC)
    result = compute_next_due_at(current, "daily", 3)
    assert result == datetime(2026, 5, 4, 9, 0, tzinfo=UTC)


def test_weekly_multiplier() -> None:
    """weekly + value=2 → 14 gün ileri."""
    current = datetime(2026, 5, 1, 9, 0, tzinfo=UTC)
    result = compute_next_due_at(current, "weekly", 2)
    assert result == datetime(2026, 5, 15, 9, 0, tzinfo=UTC)


def test_custom_days() -> None:
    """custom_days + value=45 → 45 gün ileri."""
    current = datetime(2026, 5, 1, tzinfo=UTC)
    result = compute_next_due_at(current, "custom_days", 45)
    assert result == datetime(2026, 6, 15, tzinfo=UTC)


def test_monthly_basic() -> None:
    """monthly + value=1 → sonraki ay, aynı gün."""
    current = datetime(2026, 5, 15, 9, 0, tzinfo=UTC)
    result = compute_next_due_at(current, "monthly", 1)
    assert result == datetime(2026, 6, 15, 9, 0, tzinfo=UTC)


def test_monthly_cross_year() -> None:
    """Aralık + 2 ay → Şubat sonraki yıl."""
    current = datetime(2026, 12, 10, tzinfo=UTC)
    result = compute_next_due_at(current, "monthly", 2)
    assert result == datetime(2027, 2, 10, tzinfo=UTC)


def test_monthly_end_of_month_jan_to_feb() -> None:
    """31 Ocak + 1 ay → 28 Şubat (2026 artık yıl değil)."""
    current = datetime(2026, 1, 31, 12, 0, tzinfo=UTC)
    result = compute_next_due_at(current, "monthly", 1)
    assert result == datetime(2026, 2, 28, 12, 0, tzinfo=UTC)


def test_monthly_end_of_month_leap_year() -> None:
    """31 Ocak + 1 ay → 29 Şubat (2028 artık yıl)."""
    current = datetime(2028, 1, 31, 12, 0, tzinfo=UTC)
    result = compute_next_due_at(current, "monthly", 1)
    assert result == datetime(2028, 2, 29, 12, 0, tzinfo=UTC)


def test_yearly_basic() -> None:
    """yearly + value=1 → sonraki yıl, aynı ay/gün."""
    current = datetime(2026, 6, 15, tzinfo=UTC)
    result = compute_next_due_at(current, "yearly", 1)
    assert result == datetime(2027, 6, 15, tzinfo=UTC)


def test_yearly_leap_day_to_non_leap() -> None:
    """29 Şubat 2028 + 1 yıl → 28 Şubat 2029 (artık yıl değil)."""
    current = datetime(2028, 2, 29, tzinfo=UTC)
    result = compute_next_due_at(current, "yearly", 1)
    assert result == datetime(2029, 2, 28, tzinfo=UTC)


def test_invalid_kind_raises() -> None:
    """Bilinmeyen period_kind → ValueError."""
    current = datetime(2026, 5, 1, tzinfo=UTC)
    with pytest.raises(ValueError, match="Bilinmeyen period_kind"):
        compute_next_due_at(current, "quarterly", 1)


def test_zero_value_raises() -> None:
    """value=0 → ValueError (en az 1)."""
    current = datetime(2026, 5, 1, tzinfo=UTC)
    with pytest.raises(ValueError, match="period_value en az 1 olmalı"):
        compute_next_due_at(current, "daily", 0)


def test_negative_value_raises() -> None:
    """Negatif value → ValueError."""
    current = datetime(2026, 5, 1, tzinfo=UTC)
    with pytest.raises(ValueError, match="period_value en az 1 olmalı"):
        compute_next_due_at(current, "weekly", -1)


def test_monthly_preserves_time_of_day() -> None:
    """Ay ilerlemesi saat/dakika/saniye bilgisini korur."""
    current = datetime(2026, 5, 10, 14, 30, 45, tzinfo=UTC)
    result = compute_next_due_at(current, "monthly", 3)
    assert result == datetime(2026, 8, 10, 14, 30, 45, tzinfo=UTC)


def test_monthly_day_31_to_30_day_month() -> None:
    """31 Mart + 1 ay → 30 Nisan (Nisan 30 gün)."""
    current = datetime(2026, 3, 31, tzinfo=UTC)
    result = compute_next_due_at(current, "monthly", 1)
    assert result == datetime(2026, 4, 30, tzinfo=UTC)
