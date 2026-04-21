"""Collector paralelleştirme yük testleri (F11 Paket G).

Simülatör tabanlı, in-memory (DB yok) collector yükü ölçer. DatabaseInterface
için ``AsyncMock(spec=...)`` kullanılır; collector'ın abstract methodları
çağırması mock tarafından yutulur.

Testler:
    - ``test_100_tags_1hz_no_tick_miss``: 100 tag × 1 Hz, tick miss oranı < %5
    - ``test_fast_budget_enforced_on_init``: 11 fast tag ile init → exception
    - ``test_fast_budget_accepts_at_limit``: tam bütçede init başarılı
    - ``test_parallel_vs_sequential_latency``: informatif karşılaştırma

Simülatör portu 5050 (5020 üretim, 5030 walking_skeleton, 5040+ scanner).
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock

import pytest

from custos.critical.collector import (
    FastPollingBudgetError,
    ModbusCollector,
)
from custos.shared.database import DatabaseInterface, TagRecord
from custos.shared.logging import configure_logging
from custos.simulator.modbus_server import ModbusSimulator

_LOAD_TEST_PORT = 5050
_LATENCY_TEST_PORT = 5051


@pytest.fixture(autouse=True)
def _setup_logging() -> None:
    """Test için loglama — WARNING seviyesi, log gürültüsünü azaltır."""
    configure_logging("WARNING")


def _make_tags(count: int, polling_ms: int, port: int = _LOAD_TEST_PORT) -> list[TagRecord]:
    """N adet test tag'i üretir (hepsi register 0'a işaret eder).

    Simülatör register 0 = T001 Supply Air Temp (uint16, gain 0.1). Tag
    sayısını artırmak için tag_id'ler `LOAD_0001`, `LOAD_0002`... şeklinde
    benzersiz yapılır; Modbus okumaları aynı register'a gider.
    """
    return [
        TagRecord(
            tag_id=f"LOAD_{i:04d}",
            name=f"Load test tag {i}",
            modbus_host="127.0.0.1",
            modbus_port=port,
            unit_id=1,
            register_address=0,
            register_type="uint16",
            gain=0.1,
            offset=0.0,
            unit="°C",
            polling_interval_ms=polling_ms,
            polling_preset="normal" if polling_ms <= 1000 else "slow",
        )
        for i in range(count)
    ]


def _fake_db() -> AsyncMock:
    """DatabaseInterface mock'u — insert_tag_readings_batch ve list_tags döner."""
    db = AsyncMock(spec=DatabaseInterface)
    db.insert_tag_readings_batch = AsyncMock(return_value=None)
    db.list_tags = AsyncMock(return_value=[])
    return db


async def _start_simulator(port: int) -> tuple[ModbusSimulator, asyncio.Task[None]]:
    """Simülatörü başlatır, warm-up bekler, hazır nesneleri döndürür."""
    sim = ModbusSimulator(host="127.0.0.1", port=port)
    task: asyncio.Task[None] = asyncio.create_task(sim.start())
    await asyncio.sleep(1.5)
    return sim, task


async def _stop_simulator(sim: ModbusSimulator, task: asyncio.Task[None]) -> None:
    """Simülatörü temiz durdurur."""
    sim.stop()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


# --- Bütçe enforcement testleri (hızlı, simülatörsüz) ---


def test_fast_budget_enforced_on_init() -> None:
    """11 fast tag ile init edilirse FastPollingBudgetError atılır."""
    db = _fake_db()
    tags = _make_tags(count=11, polling_ms=1000)

    with pytest.raises(FastPollingBudgetError) as exc_info:
        ModbusCollector(tags=tags, database=db, fast_polling_budget=10)

    assert "11" in str(exc_info.value)
    assert "10" in str(exc_info.value)


def test_fast_budget_accepts_at_limit() -> None:
    """Tam bütçede (10 fast tag, budget=10) init başarılı olmalı."""
    db = _fake_db()
    tags = _make_tags(count=10, polling_ms=1000)
    collector = ModbusCollector(tags=tags, database=db, fast_polling_budget=10)
    assert collector.total_tick_count == 0


def test_slow_tags_do_not_consume_budget() -> None:
    """Slow polling (>1000ms) tag'ler bütçeden saymamalı."""
    db = _fake_db()
    tags = _make_tags(count=100, polling_ms=10000)
    collector = ModbusCollector(tags=tags, database=db, fast_polling_budget=10)
    assert collector is not None


def test_mixed_fast_and_slow_counted_correctly() -> None:
    """9 fast + 50 slow: bütçe 10, geçmeli. 11 fast + 50 slow: atmalı."""
    db = _fake_db()

    ok_tags = _make_tags(count=9, polling_ms=1000) + _make_tags(count=50, polling_ms=10000)
    ModbusCollector(tags=ok_tags, database=db, fast_polling_budget=10)

    fail_tags = _make_tags(count=11, polling_ms=1000) + _make_tags(count=50, polling_ms=10000)
    with pytest.raises(FastPollingBudgetError):
        ModbusCollector(tags=fail_tags, database=db, fast_polling_budget=10)


# --- Yük testleri (simülatör gerektirir) ---


@pytest.mark.asyncio
async def test_100_tags_1hz_no_tick_miss() -> None:
    """100 tag × 1 Hz senaryosu 10 saniye çalışır, tick miss oranı < %5 olmalı.

    Tüm tag'ler aynı simülatöre işaret eder (register 0). Paralelleştirme
    ile tek tick süresi base_tick_ms (1000ms) altında kalmalı.
    """
    db = _fake_db()
    sim, sim_task = await _start_simulator(_LOAD_TEST_PORT)

    try:
        tags = _make_tags(count=100, polling_ms=1000)
        # Bütçeyi yük testi için kaldır — amaç paralelleştirmeyi ölçmek
        collector = ModbusCollector(
            tags=tags,
            database=db,
            per_host_concurrency=5,
            fast_polling_budget=1000,
        )

        collector_task = asyncio.create_task(collector.start())
        await asyncio.sleep(10)

        await collector.stop()
        await collector_task

        miss_ratio = collector.slow_tick_ratio
        total = collector.total_tick_count

        assert total > 5, f"Çok az tick gerçekleşti: {total}"
        assert miss_ratio < 0.05, (
            f"Tick miss oranı {miss_ratio:.2%} (>{0.05:.0%}); "
            f"toplam={total}, yavaş={collector._slow_tick_count}"
        )

        # DB batch insert çağrılmış mı? (mock verification)
        assert db.insert_tag_readings_batch.called, "Hiçbir batch yazılmadı"
    finally:
        await _stop_simulator(sim, sim_task)


@pytest.mark.asyncio
async def test_parallel_vs_sequential_latency() -> None:
    """İnformatif: paralel vs sequential (concurrency=1) latency karşılaştırması.

    100 tag × 1 tick: paralel (N=5) sequential'dan anlamlı ölçüde hızlı olmalı
    ama kesin threshold yok — print eder, saha tuning'inde referans olur.
    """
    db = _fake_db()
    # Farklı port (TIME_WAIT çakışmasını önlemek için)
    sim, sim_task = await _start_simulator(_LATENCY_TEST_PORT)

    try:
        tags = _make_tags(count=100, polling_ms=10000, port=_LATENCY_TEST_PORT)

        # Sequential (concurrency=1)
        seq_collector = ModbusCollector(
            tags=tags, database=db, per_host_concurrency=1, fast_polling_budget=1000
        )
        seq_collector._init_schedule()
        seq_start = time.perf_counter()
        await seq_collector._run_tick()
        seq_elapsed = time.perf_counter() - seq_start

        # Paralel (concurrency=5)
        par_collector = ModbusCollector(
            tags=tags, database=db, per_host_concurrency=5, fast_polling_budget=1000
        )
        par_collector._init_schedule()
        par_start = time.perf_counter()
        await par_collector._run_tick()
        par_elapsed = time.perf_counter() - par_start

        # Informatif — sadece log, sabit assertion yok.
        # Tek TCP socket üstünde pymodbus'ın queue davranışı nedeniyle büyük
        # speed-up beklenmez; ama paralel sequential'dan yavaş olmamalı.
        print(  # noqa: T201
            f"\n[latency] sequential={seq_elapsed * 1000:.1f}ms "
            f"parallel={par_elapsed * 1000:.1f}ms "
            f"ratio={par_elapsed / seq_elapsed:.2f}"
        )
        assert par_elapsed < seq_elapsed * 1.5, (
            "Paralel, sequential'dan anlamlı ölçüde yavaş olmamalı"
        )
    finally:
        await _stop_simulator(sim, sim_task)
