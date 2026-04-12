"""KPI Engine entegrasyon testleri.

_safe_eval fonksiyonu ve KPI hesaplama döngüsü testleri.
"""

from __future__ import annotations

import pytest

from custos.analytics.kpi_engine import _safe_eval


def test_safe_eval_basic_arithmetic() -> None:
    """Temel aritmetik işlemleri doğru hesaplamalı."""
    assert _safe_eval("a + b", {"a": 10.0, "b": 5.0}) == 15.0
    assert _safe_eval("a - b", {"a": 10.0, "b": 3.0}) == 7.0
    assert _safe_eval("a * b", {"a": 4.0, "b": 5.0}) == 20.0
    assert _safe_eval("a / b", {"a": 10.0, "b": 2.0}) == 5.0


def test_safe_eval_complex_formula() -> None:
    """Karmaşık formülleri doğru hesaplamalı."""
    # specific_energy = motor_current * 400 / flow_rate
    result = _safe_eval(
        "motor_current * 400 / flow_rate",
        {"motor_current": 15.0, "flow_rate": 100.0},
    )
    assert result is not None
    assert abs(result - 60.0) < 0.001

    # effectiveness = (hot_in - hot_out) / (hot_in - cold_in)
    result = _safe_eval(
        "(hot_in - hot_out) / (hot_in - cold_in)",
        {"hot_in": 80.0, "hot_out": 40.0, "cold_in": 20.0},
    )
    assert result is not None
    assert abs(result - 0.6667) < 0.001


def test_safe_eval_division_by_zero() -> None:
    """Sıfıra bölmede None döndürmeli."""
    assert _safe_eval("a / b", {"a": 10.0, "b": 0.0}) is None


def test_safe_eval_missing_variable() -> None:
    """Eksik değişkende None döndürmeli."""
    assert _safe_eval("a + b", {"a": 10.0}) is None


def test_safe_eval_rejects_dangerous_code() -> None:
    """Tehlikeli kodu reddetmeli — fonksiyon çağrısı, import, attribute erişimi."""
    # Fonksiyon çağrısı
    assert _safe_eval("__import__('os').system('ls')", {}) is None
    # Attribute erişimi
    assert _safe_eval("a.__class__", {"a": 1.0}) is None
    # Fonksiyon çağrısı
    assert _safe_eval("print('hack')", {}) is None
    # Lambda
    assert _safe_eval("(lambda: 1)()", {}) is None
    # List comprehension
    assert _safe_eval("[x for x in range(10)]", {}) is None


def test_safe_eval_unary_operators() -> None:
    """Unary operatörleri desteklemeli."""
    assert _safe_eval("-a", {"a": 5.0}) == -5.0
    assert _safe_eval("+a", {"a": 5.0}) == 5.0


def test_safe_eval_syntax_error() -> None:
    """Hatalı sözdiziminde None döndürmeli."""
    assert _safe_eval("a +* b", {"a": 1.0, "b": 2.0}) is None
    assert _safe_eval("", {}) is None


@pytest.mark.parametrize("formula", [
    "exec('import os')",
    "eval('1+1')",
    "open('/etc/passwd')",
    "type(42)",
    "getattr(42, '__class__')",
])
def test_safe_eval_rejects_builtins(formula: str) -> None:
    """Tüm builtin fonksiyon çağrılarını reddetmeli."""
    assert _safe_eval(formula, {}) is None
