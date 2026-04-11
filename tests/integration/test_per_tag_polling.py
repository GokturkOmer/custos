"""Per-tag polling collector testleri.

Collector'ın farklı interval'li tag'leri doğru zamanlarda
okuduğunu doğrular.
"""

from __future__ import annotations

from custos.critical.collector import _compute_base_tick_ms, _gcd
from custos.shared.database import TagRecord


def _make_tag(tag_id: str, polling_ms: int) -> TagRecord:
    """Test için TagRecord oluşturur."""
    return TagRecord(
        tag_id=tag_id,
        name=f"Test {tag_id}",
        modbus_host="127.0.0.1",
        modbus_port=5020,
        unit_id=1,
        register_address=0,
        polling_interval_ms=polling_ms,
    )


def test_gcd_basic() -> None:
    """GCD fonksiyonu doğru çalışıyor mu?"""
    assert _gcd(1000, 500) == 500
    assert _gcd(100, 1000) == 100
    assert _gcd(10000, 1000) == 1000
    assert _gcd(300, 200) == 100


def test_compute_base_tick_empty() -> None:
    """Tag listesi boşken base tick 1000ms olmalı."""
    assert _compute_base_tick_ms([]) == 1000


def test_compute_base_tick_single() -> None:
    """Tek tag varken base tick tag'in interval'ine eşit olmalı."""
    tags = [_make_tag("T1", 1000)]
    assert _compute_base_tick_ms(tags) == 1000


def test_compute_base_tick_multiple() -> None:
    """Farklı interval'li tag'ler için GCD hesaplanmalı."""
    tags = [
        _make_tag("T1", 1000),   # 1s
        _make_tag("T2", 10000),  # 10s
    ]
    assert _compute_base_tick_ms(tags) == 1000


def test_compute_base_tick_minimum() -> None:
    """Base tick minimum 50ms olmalı."""
    tags = [
        _make_tag("T1", 10),
        _make_tag("T2", 20),
    ]
    result = _compute_base_tick_ms(tags)
    assert result == 50  # Minimum sınır


def test_compute_base_tick_mixed_intervals() -> None:
    """Karışık interval'ler için GCD doğru hesaplanmalı."""
    tags = [
        _make_tag("T1", 100),    # fast
        _make_tag("T2", 1000),   # normal
        _make_tag("T3", 10000),  # slow
    ]
    assert _compute_base_tick_ms(tags) == 100
