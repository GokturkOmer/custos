"""Register gruplama algoritması unit testleri (F11 Paket I).

Test kapsamı:
    - Aynı host tek grup, farklı host ayrı grup
    - Komşu adres birleşir (40001, 40002, 40003 -> tek batch)
    - Gap toleransı (40001 + 40015 gap=13 > 8 -> ayrı batch)
    - Gap içi register'lar "dummy" alınır ama okunmaz (decode atla)
    - 125 register sınırı (büyük batch 2'ye bölünür)
    - Karma register_type (uint16 + uint32) — uint32 2 register yer kaplar
    - Boş liste -> boş sonuç
    - Negatif gap_tolerance -> ValueError

Kritik mimari kural: Bu testler gerçek DB/Modbus kullanmaz. Sadece
TagRecord dataclass ve saf algoritma kontrolü.
"""

from __future__ import annotations

import pytest

from custos.critical.batch_grouper import (
    MAX_REGISTERS_PER_BATCH,
    BatchGroup,
    group_tags_for_batch_read,
)
from custos.shared.database import TagRecord


def _make_tag(
    tag_id: str,
    address: int,
    *,
    host: str = "10.0.0.1",
    port: int = 502,
    unit_id: int = 1,
    register_type: str = "uint16",
) -> TagRecord:
    """Test için minimum TagRecord üretir."""
    return TagRecord(
        tag_id=tag_id,
        name=f"test {tag_id}",
        modbus_host=host,
        modbus_port=port,
        unit_id=unit_id,
        register_address=address,
        register_type=register_type,
        gain=1.0,
        offset=0.0,
        unit="",
        polling_interval_ms=1000,
        polling_preset="normal",
    )


def test_empty_tag_list_returns_empty() -> None:
    """Boş girdi boş sonuç döner."""
    assert group_tags_for_batch_read([], gap_tolerance=8) == []


def test_negative_gap_tolerance_raises() -> None:
    """Negatif gap_tolerance ValueError atar."""
    with pytest.raises(ValueError, match="negatif"):
        group_tags_for_batch_read([_make_tag("T1", 0)], gap_tolerance=-1)


def test_contiguous_addresses_merge_into_single_batch() -> None:
    """40001, 40002, 40003 -> tek batch (count=3)."""
    tags = [
        _make_tag("T1", 40001),
        _make_tag("T2", 40002),
        _make_tag("T3", 40003),
    ]

    batches = group_tags_for_batch_read(tags, gap_tolerance=8)

    assert len(batches) == 1
    assert batches[0].start_address == 40001
    assert batches[0].count == 3
    assert batches[0].tag_count == 3


def test_gap_within_tolerance_still_merges() -> None:
    """Adres 10 + 15 arası 4 register boşluk, gap_tolerance=8 -> tek batch."""
    tags = [_make_tag("T1", 10), _make_tag("T2", 15)]

    batches = group_tags_for_batch_read(tags, gap_tolerance=8)

    assert len(batches) == 1
    assert batches[0].start_address == 10
    assert batches[0].count == 6  # 10..15 dahil
    assert batches[0].end_address == 15


def test_gap_above_tolerance_splits_batches() -> None:
    """Adres 10 + 30 arası 19 register boşluk, gap_tolerance=8 -> iki batch."""
    tags = [_make_tag("T1", 10), _make_tag("T2", 30)]

    batches = group_tags_for_batch_read(tags, gap_tolerance=8)

    assert len(batches) == 2
    addresses = sorted(b.start_address for b in batches)
    assert addresses == [10, 30]
    assert all(b.count == 1 for b in batches)


def test_different_hosts_never_merge() -> None:
    """Aynı adres, farklı host -> ayrı batch."""
    tags = [
        _make_tag("T1", 100, host="10.0.0.1"),
        _make_tag("T2", 100, host="10.0.0.2"),
    ]

    batches = group_tags_for_batch_read(tags, gap_tolerance=8)

    assert len(batches) == 2
    hosts = sorted(b.modbus_host for b in batches)
    assert hosts == ["10.0.0.1", "10.0.0.2"]


def test_different_ports_never_merge() -> None:
    """Aynı host+adres, farklı port -> ayrı batch."""
    tags = [
        _make_tag("T1", 100, port=502),
        _make_tag("T2", 100, port=503),
    ]

    batches = group_tags_for_batch_read(tags, gap_tolerance=8)

    assert len(batches) == 2


def test_different_unit_ids_never_merge() -> None:
    """Aynı host+port, farklı unit_id -> ayrı batch (farklı slave)."""
    tags = [
        _make_tag("T1", 100, unit_id=1),
        _make_tag("T2", 100, unit_id=2),
    ]

    batches = group_tags_for_batch_read(tags, gap_tolerance=8)

    assert len(batches) == 2


def test_max_batch_size_splits_large_block() -> None:
    """Ardışık 150 register -> 125 + 25 olarak ikiye bölünür."""
    tags = [_make_tag(f"T{i}", i, register_type="uint16") for i in range(150)]

    batches = group_tags_for_batch_read(tags, gap_tolerance=8)

    assert len(batches) == 2
    # Her batch MAX sınırını aşmamalı
    assert all(b.count <= MAX_REGISTERS_PER_BATCH for b in batches)
    first, second = sorted(batches, key=lambda b: b.start_address)
    assert first.count == MAX_REGISTERS_PER_BATCH
    assert second.count == 150 - MAX_REGISTERS_PER_BATCH
    # Toplam tag sayısı korunmalı
    assert sum(b.tag_count for b in batches) == 150


def test_uint32_tag_occupies_two_registers() -> None:
    """uint32 tag 2 register yer kaplar; count buna göre hesaplanır."""
    tags = [_make_tag("T1", 10, register_type="uint32")]

    batches = group_tags_for_batch_read(tags, gap_tolerance=8)

    assert len(batches) == 1
    assert batches[0].start_address == 10
    assert batches[0].count == 2  # 10 ve 11
    assert batches[0].end_address == 11


def test_float32_tag_occupies_two_registers() -> None:
    """float32 tag 2 register yer kaplar."""
    tags = [_make_tag("T1", 20, register_type="float32")]

    batches = group_tags_for_batch_read(tags, gap_tolerance=8)

    assert len(batches) == 1
    assert batches[0].count == 2


def test_mixed_register_types_group_correctly() -> None:
    """uint16(10) + uint32(11,12) + uint16(13) ardışık -> tek batch, count=4.

    uint32 11 adresinden başlayıp 12'yi de kullanır. Sonraki tag 13'te başlar
    (gap=0), batch'e eklenir.
    """
    tags = [
        _make_tag("T1", 10, register_type="uint16"),
        _make_tag("T2", 11, register_type="uint32"),
        _make_tag("T3", 13, register_type="uint16"),
    ]

    batches = group_tags_for_batch_read(tags, gap_tolerance=8)

    assert len(batches) == 1
    assert batches[0].start_address == 10
    assert batches[0].count == 4  # 10..13 dahil
    assert batches[0].tag_count == 3


def test_unordered_input_is_sorted_internally() -> None:
    """Sıralanmamış girdi içeride register_address'e göre sıralanır."""
    tags = [
        _make_tag("T3", 30),
        _make_tag("T1", 10),
        _make_tag("T2", 12),
    ]

    batches = group_tags_for_batch_read(tags, gap_tolerance=8)

    # 10 + 12 birleşir (gap=1), 30 ayrı (gap=17)
    assert len(batches) == 2
    small_batch = next(b for b in batches if b.start_address == 10)
    large_batch = next(b for b in batches if b.start_address == 30)
    assert small_batch.count == 3
    assert large_batch.count == 1


def test_zero_gap_tolerance_only_merges_contiguous() -> None:
    """gap_tolerance=0 -> sadece tam ardışık adresler birleşir."""
    tags = [
        _make_tag("T1", 10),
        _make_tag("T2", 11),  # ardışık
        _make_tag("T3", 13),  # 1 gap -> yeni batch
    ]

    batches = group_tags_for_batch_read(tags, gap_tolerance=0)

    assert len(batches) == 2
    batch1 = next(b for b in batches if b.start_address == 10)
    batch2 = next(b for b in batches if b.start_address == 13)
    assert batch1.count == 2
    assert batch2.count == 1


def test_batch_group_properties() -> None:
    """BatchGroup end_address ve tag_count doğru hesaplanır."""
    tag = _make_tag("T1", 100)
    bg = BatchGroup(
        modbus_host="10.0.0.1",
        modbus_port=502,
        unit_id=1,
        start_address=100,
        count=10,
        tags=[tag],
    )

    assert bg.end_address == 109
    assert bg.tag_count == 1
