"""Collector _read_tag yolları için birim testler (denetim A2 kapsamı).

Modbus client'ı tamamen mock'lanır — gerçek TCP yok. Hedef: bağlantı
kurulamama, Modbus error response, beklenmeyen exception, başarılı okuma
ve gain/offset uygulaması yollarının coverage'a girmesi.

Kritik path: collector.py içinde 3 ana hata bayrağı (quality_flag=1)
yolu ve 1 başarı yolu — bunlar daha önce yalnızca walking skeleton ve
yük testlerinde dolaylı kapsanıyordu.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from custos.critical.collector import ModbusCollector
from custos.shared.database import DatabaseInterface, TagRecord


def _fake_db() -> AsyncMock:
    """DatabaseInterface mock'u — sadece batch insert + list_tags tanımlı."""
    db = AsyncMock(spec=DatabaseInterface)
    db.insert_tag_readings_batch = AsyncMock(return_value=None)
    db.list_tags = AsyncMock(return_value=[])
    return db


def _make_tag(tag_id: str = "T_READ_1") -> TagRecord:
    """Standart test tag'i — gain 0.1, offset 5.0 ile ölçeklendirme doğrulanır."""
    return TagRecord(
        tag_id=tag_id,
        name="read path test",
        modbus_host="127.0.0.1",
        modbus_port=5099,
        unit_id=1,
        register_address=0,
        register_type="uint16",
        gain=0.1,
        offset=5.0,
        unit="°C",
        polling_interval_ms=1000,
        polling_preset="normal",
    )


def _make_collector(tag: TagRecord) -> ModbusCollector:
    """Tek tag'li collector — fast budget yüksek tutulur, tag aktif kabul edilir."""
    return ModbusCollector(
        tags=[tag],
        database=_fake_db(),
        per_host_concurrency=1,
        fast_polling_budget=100,
    )


def _install_mock_client(collector: ModbusCollector, client: MagicMock) -> None:
    """Collector'ın client cache'ine mock client yerleştirir.

    `_get_or_create_client` çağrısı bu cache'ten dönecek, gerçek TCP
    bağlantısı denenmeyecek.
    """
    tag = collector._tags[0]
    collector._clients[(tag.modbus_host, tag.modbus_port)] = client  # type: ignore[assignment]


@pytest.mark.asyncio
async def test_read_tag_returns_quality_flag_when_connect_fails() -> None:
    """Bağlantı kurulamayan tag quality_flag=1 + value=0.0 döndürmeli."""
    tag = _make_tag()
    collector = _make_collector(tag)

    client = MagicMock()
    client.connected = False
    client.connect = AsyncMock(return_value=False)
    _install_mock_client(collector, client)

    reading = await collector._read_tag(tag)

    assert reading.tag_id == tag.tag_id
    assert reading.quality_flag == 1
    assert reading.value == 0.0
    # Read denenmemeli — bağlantı yoksa erken dönüş
    assert not hasattr(client, "read_holding_registers") or not getattr(
        client.read_holding_registers, "called", False
    )


@pytest.mark.asyncio
async def test_read_tag_marks_quality_flag_when_response_is_error() -> None:
    """Modbus exception response (isError True) quality_flag=1 ile işaretlenmeli."""
    tag = _make_tag("T_READ_2")
    collector = _make_collector(tag)

    bad_response: Any = MagicMock()
    bad_response.isError = MagicMock(return_value=True)
    bad_response.__str__ = lambda self: "ModbusIOException(GatewayPathUnavailable)"

    client = MagicMock()
    client.connected = True
    client.read_holding_registers = AsyncMock(return_value=bad_response)
    _install_mock_client(collector, client)

    reading = await collector._read_tag(tag)

    assert reading.quality_flag == 1
    assert reading.value == 0.0
    client.read_holding_registers.assert_awaited_once()


@pytest.mark.asyncio
async def test_read_tag_marks_quality_flag_on_unexpected_exception() -> None:
    """read_holding_registers exception fırlatırsa graceful quality_flag=1."""
    tag = _make_tag("T_READ_3")
    collector = _make_collector(tag)

    client = MagicMock()
    client.connected = True
    client.read_holding_registers = AsyncMock(side_effect=ConnectionResetError("peer reset"))
    _install_mock_client(collector, client)

    reading = await collector._read_tag(tag)

    assert reading.quality_flag == 1
    assert reading.value == 0.0


@pytest.mark.asyncio
async def test_read_tag_applies_gain_and_offset_on_success() -> None:
    """Başarılı okumada raw_value * gain + offset doğru hesaplanmalı."""
    tag = _make_tag("T_READ_4")  # gain=0.1, offset=5.0
    collector = _make_collector(tag)

    good_response: Any = MagicMock()
    good_response.isError = MagicMock(return_value=False)
    good_response.registers = [250]  # 250 * 0.1 + 5.0 = 30.0

    client = MagicMock()
    client.connected = True
    client.read_holding_registers = AsyncMock(return_value=good_response)
    _install_mock_client(collector, client)

    reading = await collector._read_tag(tag)

    assert reading.quality_flag == 0
    assert reading.value == pytest.approx(30.0)


@pytest.mark.asyncio
async def test_refresh_tags_updates_schedule_for_added_and_removed() -> None:
    """list_tags yeni/silinen tag dönerse _next_due ve _tags güncellenmeli."""
    tag_old = _make_tag("T_REFRESH_OLD")
    collector = _make_collector(tag_old)
    collector._init_schedule()
    assert "T_REFRESH_OLD" in collector._next_due

    tag_new = _make_tag("T_REFRESH_NEW")
    db = collector._database
    assert isinstance(db, AsyncMock)
    db.list_tags = AsyncMock(return_value=[tag_new])

    await collector._refresh_tags()

    assert {t.tag_id for t in collector._tags} == {"T_REFRESH_NEW"}
    assert "T_REFRESH_NEW" in collector._next_due
    assert "T_REFRESH_OLD" not in collector._next_due
