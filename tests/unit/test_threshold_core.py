"""shared/threshold_core.py birim testleri (DB gerektirmez).

Eşik tabanlı alarm karar mantığının saf çekirdeği: breach tespiti, hysteresis
ile temizleme, emergency debounce kısması. Bu mantık Critical loop'a taşınan
alarm üretiminin kalbidir (review H1); sınır koşulları burada kilitlenir.
"""

from __future__ import annotations

from custos.shared.database import Threshold
from custos.shared.threshold_core import (
    can_clear_with_hysteresis,
    effective_debounce_seconds,
    is_breach,
)


def _threshold(
    *,
    direction: str = "high",
    set_point: float = 100.0,
    hysteresis: float = 0.0,
    severity: str = "warn",
    debounce_seconds: int = 5,
) -> Threshold:
    """Test için minimal Threshold üretir."""
    return Threshold(
        tag_id="tag_1",
        name="test",
        direction=direction,
        set_point=set_point,
        hysteresis=hysteresis,
        severity=severity,
        debounce_seconds=debounce_seconds,
    )


# --- is_breach ---


def test_is_breach_high_above_set_point() -> None:
    """High yön: set_point üstü breach."""
    assert is_breach(_threshold(direction="high", set_point=100.0), 100.1) is True


def test_is_breach_high_at_set_point_is_breach() -> None:
    """High yön: tam set_point (>=) breach sayılır."""
    assert is_breach(_threshold(direction="high", set_point=100.0), 100.0) is True


def test_is_breach_high_below_set_point_not_breach() -> None:
    """High yön: set_point altı breach değil."""
    assert is_breach(_threshold(direction="high", set_point=100.0), 99.9) is False


def test_is_breach_low_below_set_point() -> None:
    """Low yön: set_point altı breach."""
    assert is_breach(_threshold(direction="low", set_point=10.0), 9.9) is True


def test_is_breach_low_at_set_point_is_breach() -> None:
    """Low yön: tam set_point (<=) breach sayılır."""
    assert is_breach(_threshold(direction="low", set_point=10.0), 10.0) is True


def test_is_breach_low_above_set_point_not_breach() -> None:
    """Low yön: set_point üstü breach değil."""
    assert is_breach(_threshold(direction="low", set_point=10.0), 10.1) is False


# --- can_clear_with_hysteresis ---


def test_clear_high_below_dead_band() -> None:
    """High yön: set_point - hysteresis altına düşünce temizlenir."""
    thr = _threshold(direction="high", set_point=100.0, hysteresis=5.0)
    assert can_clear_with_hysteresis(thr, 94.9) is True


def test_clear_high_inside_dead_band_no_clear() -> None:
    """High yön: ölü bant içinde (set_point - hysteresis sınırında) temizlenmez."""
    thr = _threshold(direction="high", set_point=100.0, hysteresis=5.0)
    # value == set_point - hysteresis (95.0): kesin '<' olmadığından temizlenmez
    assert can_clear_with_hysteresis(thr, 95.0) is False
    assert can_clear_with_hysteresis(thr, 97.0) is False


def test_clear_low_above_dead_band() -> None:
    """Low yön: set_point + hysteresis üstüne çıkınca temizlenir."""
    thr = _threshold(direction="low", set_point=10.0, hysteresis=2.0)
    assert can_clear_with_hysteresis(thr, 12.1) is True


def test_clear_low_inside_dead_band_no_clear() -> None:
    """Low yön: ölü bant içinde temizlenmez (set_point + hysteresis sınırı dahil)."""
    thr = _threshold(direction="low", set_point=10.0, hysteresis=2.0)
    assert can_clear_with_hysteresis(thr, 12.0) is False
    assert can_clear_with_hysteresis(thr, 11.0) is False


def test_breach_and_clear_asymmetry_at_zero_hysteresis() -> None:
    """hysteresis=0: tam set_point'te breach sürer, temizlenmez (asimetri)."""
    thr = _threshold(direction="high", set_point=100.0, hysteresis=0.0)
    assert is_breach(thr, 100.0) is True
    assert can_clear_with_hysteresis(thr, 100.0) is False
    # Bir tık altı: breach biter ve temizlenebilir.
    assert is_breach(thr, 99.999) is False
    assert can_clear_with_hysteresis(thr, 99.999) is True


# --- effective_debounce_seconds ---


def test_debounce_normal_severity_unchanged() -> None:
    """warn/crit severity: yapılandırılmış debounce aynen kullanılır."""
    assert effective_debounce_seconds(_threshold(severity="warn", debounce_seconds=30)) == 30
    assert effective_debounce_seconds(_threshold(severity="crit", debounce_seconds=0)) == 0


def test_debounce_emergency_capped_to_one() -> None:
    """emergency severity: debounce en fazla 1 sn'ye kısılır."""
    assert effective_debounce_seconds(
        _threshold(severity="emergency", debounce_seconds=60)
    ) == 1


def test_debounce_emergency_zero_stays_zero() -> None:
    """emergency + debounce 0: min(1, 0) = 0 (anında)."""
    assert effective_debounce_seconds(
        _threshold(severity="emergency", debounce_seconds=0)
    ) == 0
