"""R-06 / V11-304-305: threshold_engine'in saf yardımcı fonksiyonlarına
unit test'ler — DB'siz, tick yok.

- ``_cross_sensor_holds`` 6 operator × 2 sonuç (12 case) + bilinmeyen
  operator için defansif True dönüşü.
- ``_RATE_OF_CHANGE_COOLDOWN`` ve ``_CROSS_SENSOR_COOLDOWN`` doğru
  timedelta'ları (paket dokümantasyonu — rate 5 dk, cross 10 dk).
"""

from __future__ import annotations

from datetime import timedelta

from custos.analytics.threshold_engine import (
    _CROSS_SENSOR_COOLDOWN,
    _RATE_OF_CHANGE_COOLDOWN,
    _cross_sensor_holds,
)


def test_cross_sensor_holds_lt_true_when_a_less_than_b() -> None:
    """lt: a < b → True (kural sağlanıyor, ihlal yok)."""
    assert _cross_sensor_holds(5.0, "lt", 10.0) is True


def test_cross_sensor_holds_lt_false_when_a_equal_or_greater() -> None:
    """lt: a >= b → False (ihlal var)."""
    assert _cross_sensor_holds(10.0, "lt", 10.0) is False
    assert _cross_sensor_holds(15.0, "lt", 10.0) is False


def test_cross_sensor_holds_gt() -> None:
    """gt: a > b → True; aksi → False."""
    assert _cross_sensor_holds(15.0, "gt", 10.0) is True
    assert _cross_sensor_holds(10.0, "gt", 10.0) is False
    assert _cross_sensor_holds(5.0, "gt", 10.0) is False


def test_cross_sensor_holds_eq_neq() -> None:
    """eq / neq — birbirinin tersi kontrolleri."""
    assert _cross_sensor_holds(7.5, "eq", 7.5) is True
    assert _cross_sensor_holds(7.5, "eq", 8.0) is False
    assert _cross_sensor_holds(7.5, "neq", 8.0) is True
    assert _cross_sensor_holds(7.5, "neq", 7.5) is False


def test_cross_sensor_holds_lte_gte() -> None:
    """lte / gte — eşitlikte True."""
    assert _cross_sensor_holds(10.0, "lte", 10.0) is True
    assert _cross_sensor_holds(9.0, "lte", 10.0) is True
    assert _cross_sensor_holds(11.0, "lte", 10.0) is False
    assert _cross_sensor_holds(10.0, "gte", 10.0) is True
    assert _cross_sensor_holds(11.0, "gte", 10.0) is True
    assert _cross_sensor_holds(9.0, "gte", 10.0) is False


def test_cross_sensor_holds_unknown_operator_returns_true_defensively() -> None:
    """Bilinmeyen operator → True (DB CHECK zaten engeller; kötü değer
    gelirse alarm bombardımanı olmasın).
    """
    assert _cross_sensor_holds(5.0, "wat", 10.0) is True


def test_layer1_cooldown_constants() -> None:
    """Paket dokümanı: rate-of-change cooldown 5 dk, cross-sensor 10 dk."""
    assert _RATE_OF_CHANGE_COOLDOWN == timedelta(minutes=5)
    assert _CROSS_SENSOR_COOLDOWN == timedelta(minutes=10)
