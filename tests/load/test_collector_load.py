"""Collector paralelleştirme ve batch read yük testleri (F11 Paket G+I).

Simülatör tabanlı, in-memory (DB yok) collector yükü ölçer. DatabaseInterface
için ``AsyncMock(spec=...)`` kullanılır; collector'ın abstract methodları
çağırması mock tarafından yutulur.

Testler:
    - ``test_100_tags_1hz_no_tick_miss``: 100 tag × 1 Hz, tick miss < %5
    - ``test_200_tags_batch_read_tick_miss``: 200 tag karma polling, batch
      read ile tick miss < %5 (Paket I)
    - ``test_batch_fallback_on_partial_error``: batch isError -> per-tag
      fallback; tek bozuk tag, diğerleri başarılı (Paket I)
    - ``test_mixed_register_types``: uint16 + uint32 + float32 karma
      tag setinde batch decode doğru (Paket I)
    - ``test_fast_budget_enforced_on_init``: 11 fast tag -> exception
    - ``test_fast_budget_accepts_at_limit``: tam bütçede init başarılı
    - ``test_parallel_vs_sequential_latency``: informatif karşılaştırma

Simülatör portu 5050 (5020 üretim, 5030 walking_skeleton, 5040+ scanner).
Paket I testleri 5052 kullanır (TIME_WAIT çakışması önlemek için).
"""

from __future__ import annotations

import asyncio
import struct
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

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
_BATCH_LOAD_TEST_PORT = 5052


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


# --- F11 Paket I: Batch Modbus Read testleri ---


def _make_distributed_tags(
    count: int,
    polling_ms: int,
    *,
    port: int,
    address_range: int = 30,
    register_type: str = "uint16",
) -> list[TagRecord]:
    """N adet tag'i 0..address_range-1 adreslerine dağıtır.

    Simülatör 30 register (0-29) sunar; batch path'te bu aralık
    gap_tolerance=8 ile tek batch olarak okunur.
    """
    return [
        TagRecord(
            tag_id=f"DIST_{i:04d}",
            name=f"Distributed tag {i}",
            modbus_host="127.0.0.1",
            modbus_port=port,
            unit_id=1,
            register_address=i % address_range,
            register_type=register_type,
            gain=0.1,
            offset=0.0,
            unit="°C",
            polling_interval_ms=polling_ms,
            polling_preset="normal" if polling_ms <= 1000 else "slow",
        )
        for i in range(count)
    ]


@pytest.mark.asyncio
async def test_200_tags_batch_read_tick_miss() -> None:
    """200 tag karma polling (150 slow + 45 normal + 5 fast) batch read.

    Tüm tag'ler 0-29 adres aralığına dağılır; batch path tek okuma çağrısı
    yapar (gap_tolerance=8 ile hepsi tek batch). Hedef: tick miss < %5.
    """
    db = _fake_db()
    sim, sim_task = await _start_simulator(_BATCH_LOAD_TEST_PORT)

    try:
        slow_tags = _make_distributed_tags(150, polling_ms=10_000, port=_BATCH_LOAD_TEST_PORT)
        normal_tags = _make_distributed_tags(45, polling_ms=1_000, port=_BATCH_LOAD_TEST_PORT)
        fast_tags = _make_distributed_tags(5, polling_ms=500, port=_BATCH_LOAD_TEST_PORT)
        tags = slow_tags + normal_tags + fast_tags

        collector = ModbusCollector(
            tags=tags,
            database=db,
            per_host_concurrency=5,
            fast_polling_budget=1000,  # yük testi için bütçeyi aç
            batch_read_enabled=True,
            batch_gap_tolerance=8,
        )

        collector_task = asyncio.create_task(collector.start())
        await asyncio.sleep(10)

        await collector.stop()
        await collector_task

        miss_ratio = collector.slow_tick_ratio
        total = collector.total_tick_count
        batch_count = collector._batch_read_count
        single_count = collector._single_read_count

        assert total > 5, f"Çok az tick gerçekleşti: {total}"
        assert miss_ratio < 0.05, (
            f"200 tag batch read'te tick miss {miss_ratio:.2%}; "
            f"batch={batch_count}, single={single_count}, toplam={total}"
        )
        # Batch path aktif olduğunun kanıtı: batch sayısı single'dan fazla
        # olmalı (fallback yoksa single=0).
        assert batch_count > 0, "Batch read hiç çalışmadı"
        assert db.insert_tag_readings_batch.called
    finally:
        await _stop_simulator(sim, sim_task)


@pytest.mark.asyncio
async def test_batch_fallback_on_partial_error() -> None:
    """Batch response.isError() -> per-tag fallback; tüm tag'ler okunabilmeli.

    Modbus client mock'lanır: read_holding_registers ilk çağrıda error
    döner (batch), sonraki per-tag çağrılarında başarılı register döner.
    """
    db = _fake_db()
    tags = _make_distributed_tags(
        10, polling_ms=1_000, port=_BATCH_LOAD_TEST_PORT, address_range=10
    )

    collector = ModbusCollector(
        tags=tags,
        database=db,
        per_host_concurrency=5,
        fast_polling_budget=100,
        batch_read_enabled=True,
    )

    # Fake client: batch call error, single call OK.
    fake_client = MagicMock()
    fake_client.connected = True
    fake_client.connect = AsyncMock(return_value=True)
    fake_client.close = MagicMock()

    batch_error_response = MagicMock()
    batch_error_response.isError = MagicMock(return_value=True)
    batch_error_response.__str__ = MagicMock(return_value="Modbus slave busy")

    single_ok_response = MagicMock()
    single_ok_response.isError = MagicMock(return_value=False)
    single_ok_response.registers = [1234]

    call_log: list[int] = []

    async def _read_side_effect(address: int, *, count: int = 1, device_id: int = 1) -> Any:
        call_log.append(count)
        if count > 1:
            return batch_error_response
        return single_ok_response

    fake_client.read_holding_registers = AsyncMock(side_effect=_read_side_effect)

    with patch.object(collector, "_get_or_create_client", new=AsyncMock(return_value=fake_client)):
        collector._init_schedule()
        await collector._run_tick()

    # Atomicity doğrulama
    readings_batches = db.insert_tag_readings_batch.call_args_list
    assert readings_batches, "Hiç batch yazılmadı"
    last_readings = readings_batches[-1].args[0]
    assert len(last_readings) == 10, "10 tag'in hepsi bir reading üretmeli"
    ok = sum(1 for r in last_readings if r.quality_flag == 0)
    assert ok == 10, (
        f"Fallback sonrası 10/10 başarı bekleniyor, {ok}/10 alındı. Call log counts: {call_log}"
    )
    assert collector._batch_fallback_count >= 1, (
        "Fallback sayacı artmalı (batch error sonrası per-tag retry)"
    )
    # Batch bir kez denendi, sonra 10 single call
    batch_calls = sum(1 for c in call_log if c > 1)
    single_calls = sum(1 for c in call_log if c == 1)
    assert batch_calls >= 1 and single_calls == 10, (
        f"Batch=1, single=10 bekleniyor; gerçek batch={batch_calls}, single={single_calls}"
    )


@pytest.mark.asyncio
async def test_mixed_register_types() -> None:
    """uint16 + uint32 + float32 karma tag setinde batch decode doğru değer.

    Batch 0'dan 6 register okunur:
        reg 0-1: uint16 (x2)
        reg 2-3: uint32 = 0x00010002 = 65538
        reg 4-5: float32 = 1.0

    Tag'ler:
        T_U16_A (addr=0, uint16): raw=100 -> 100.0
        T_U16_B (addr=1, uint16): raw=200 -> 200.0
        T_U32   (addr=2, uint32): 65538 -> 65538.0
        T_FLOAT (addr=4, float32): 1.0 -> 1.0
    """
    db = _fake_db()

    def _t(
        tag_id: str,
        address: int,
        register_type: str,
    ) -> TagRecord:
        return TagRecord(
            tag_id=tag_id,
            name=tag_id,
            modbus_host="127.0.0.1",
            modbus_port=_BATCH_LOAD_TEST_PORT,
            unit_id=1,
            register_address=address,
            register_type=register_type,
            byte_order="big",
            gain=1.0,
            offset=0.0,
            polling_interval_ms=1_000,
            polling_preset="normal",
        )

    tags = [
        _t("T_U16_A", 0, "uint16"),
        _t("T_U16_B", 1, "uint16"),
        _t("T_U32", 2, "uint32"),
        _t("T_FLOAT", 4, "float32"),
    ]

    # Beklenen batch okuma: start=0, count=6. float32(1.0) = 0x3F800000.
    packed_float = struct.pack(">f", 1.0)
    hi_f, lo_f = struct.unpack(">HH", packed_float)
    fake_registers = [100, 200, 0x0001, 0x0002, hi_f, lo_f]

    fake_response = MagicMock()
    fake_response.isError = MagicMock(return_value=False)
    fake_response.registers = fake_registers

    fake_client = MagicMock()
    fake_client.connected = True
    fake_client.connect = AsyncMock(return_value=True)
    fake_client.close = MagicMock()
    fake_client.read_holding_registers = AsyncMock(return_value=fake_response)

    collector = ModbusCollector(
        tags=tags,
        database=db,
        fast_polling_budget=10,
        batch_read_enabled=True,
    )

    with patch.object(collector, "_get_or_create_client", new=AsyncMock(return_value=fake_client)):
        collector._init_schedule()
        await collector._run_tick()

    # Tek batch çağrısı, count=6
    assert fake_client.read_holding_registers.call_count == 1
    args, kwargs = fake_client.read_holding_registers.call_args
    assert kwargs.get("count") == 6

    # Decode doğrulaması
    readings_batches = db.insert_tag_readings_batch.call_args_list
    assert readings_batches, "Batch yazılmadı"
    readings = {r.tag_id: r for r in readings_batches[-1].args[0]}
    assert readings["T_U16_A"].value == pytest.approx(100.0)
    assert readings["T_U16_B"].value == pytest.approx(200.0)
    assert readings["T_U32"].value == pytest.approx(65538.0)
    assert readings["T_FLOAT"].value == pytest.approx(1.0, abs=1e-5)
    assert all(r.quality_flag == 0 for r in readings.values())
    assert collector._batch_read_count == 1


@pytest.mark.asyncio
async def test_batch_read_disabled_falls_back_to_single() -> None:
    """batch_read_enabled=False -> eski single-read path, single_count artar."""
    db = _fake_db()
    tags = _make_distributed_tags(5, polling_ms=1_000, port=_BATCH_LOAD_TEST_PORT, address_range=5)

    collector = ModbusCollector(
        tags=tags,
        database=db,
        fast_polling_budget=100,
        batch_read_enabled=False,
    )

    fake_client = MagicMock()
    fake_client.connected = True
    fake_client.connect = AsyncMock(return_value=True)
    fake_client.close = MagicMock()

    ok_response = MagicMock()
    ok_response.isError = MagicMock(return_value=False)
    ok_response.registers = [0]
    fake_client.read_holding_registers = AsyncMock(return_value=ok_response)

    with patch.object(collector, "_get_or_create_client", new=AsyncMock(return_value=fake_client)):
        collector._init_schedule()
        await collector._run_tick()

    assert collector._batch_read_count == 0
    assert collector._single_read_count == 5
    # Feature flag kapalıyken 5 single çağrı
    assert fake_client.read_holding_registers.call_count == 5
