"""V11-000-B kapsamı: Collector tick_miss telemetri property'leri.

`slow_tick_*` mevcut yük testleri ve brief sözleşmesi için saklanır;
yeni `tick_miss_count` ve `tick_miss_ratio` alias property'leri
endurance_metrics scriptinin journalctl'den okuyacağı kanonik
isimlerle aynı sayıyı döndürür. Üç senaryo: hiç tick yokken sıfır,
mevcut slow tick sayısını yansıtması, ve oran hesabının kayan
nokta tutarlılığı.

Periyodik "Tick özet" eventi test edilmiyor — start() ana
döngüsünü çalıştırmadan property değerleri doğrulamak yeterli;
event yazımı integration veya yük testleriyle dolaylı olarak
kapsanır.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from custos.critical.collector import ModbusCollector
from custos.shared.database import DatabaseInterface, TagRecord


def _fake_db() -> AsyncMock:
    """Minimum DatabaseInterface mock — collector init için yeterli."""
    db = AsyncMock(spec=DatabaseInterface)
    db.insert_tag_readings_batch = AsyncMock(return_value=None)
    db.list_tags = AsyncMock(return_value=[])
    return db


def _make_tag() -> TagRecord:
    """Tek tag — fast budget içinde, init engelsiz geçsin."""
    return TagRecord(
        tag_id="T_TICK_1",
        name="tick miss telemetri",
        modbus_host="127.0.0.1",
        modbus_port=5099,
        unit_id=1,
        register_address=0,
        register_type="uint16",
        gain=1.0,
        offset=0.0,
        unit="",
        polling_interval_ms=1000,
        polling_preset="normal",
    )


def _make_collector() -> ModbusCollector:
    return ModbusCollector(
        tags=[_make_tag()],
        database=_fake_db(),
        per_host_concurrency=1,
        fast_polling_budget=100,
    )


def test_tick_miss_count_alias_equals_slow_tick_count() -> None:
    """`tick_miss_count` property'si `_slow_tick_count` field'ını döner."""
    collector = _make_collector()
    assert collector.tick_miss_count == 0

    collector._slow_tick_count = 7
    assert collector.tick_miss_count == 7


def test_tick_miss_ratio_alias_matches_slow_tick_ratio() -> None:
    """`tick_miss_ratio` property'si `slow_tick_ratio` ile aynı sayı."""
    collector = _make_collector()
    collector._total_tick_count = 200
    collector._slow_tick_count = 5

    assert collector.tick_miss_ratio == collector.slow_tick_ratio
    assert collector.tick_miss_ratio == 0.025


def test_tick_miss_ratio_zero_when_no_ticks_yet() -> None:
    """Hiç tick yapılmamış collector'da oran 0.0 (bölme güvenliği)."""
    collector = _make_collector()
    assert collector.total_tick_count == 0
    assert collector.tick_miss_count == 0
    assert collector.tick_miss_ratio == 0.0
