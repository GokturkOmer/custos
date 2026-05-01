"""Liveness engine saf yardımcı fonksiyonlarına unit testler — DB'siz, tick yok.

``_check_counter`` davranışı:

- uint16 rollover (65535 → 0) sahte alarm olarak okunmamalı; eşikler
  ``first > 60000 AND last < 5000``. Diğer azalmalar gerçek sayaç-geri
  gitti olarak alarm tetikler.
- Sayaç durağansa (pencere boyu eşiği aşmış, değer aynı) "Counter durağan"
  mesajı döner — bu davranış v1.0.1 fix'inden önce de vardı, regresyon
  yaşamadığını doğrulamak için tutuyoruz.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from custos.analytics.liveness_engine import (
    _COUNTER_ROLLOVER_HIGH_THRESHOLD,
    _COUNTER_ROLLOVER_LOW_THRESHOLD,
    _check_counter,
)
from custos.shared.database import TagReading


def _make_readings(
    values: list[float],
    *,
    start: datetime | None = None,
    spacing_seconds: int = 60,
    tag_id: str = "TAG_TEST",
) -> list[TagReading]:
    """Eşit aralıklı TagReading listesi üretir."""
    base = start or datetime(2026, 5, 2, 12, 0, 0, tzinfo=UTC)
    return [
        TagReading(
            timestamp=base + timedelta(seconds=i * spacing_seconds),
            tag_id=tag_id,
            value=v,
        )
        for i, v in enumerate(values)
    ]


@pytest.mark.asyncio
async def test_counter_rollover_no_alarm() -> None:
    """uint16 rollover (63000 → 1700) sahte alarma yol açmamalı."""
    readings = _make_readings([63000.0, 63500.0, 64200.0, 1700.0])
    message = await _check_counter(readings, seconds=300, tag_id="KWH_01")
    assert message is None


@pytest.mark.asyncio
async def test_counter_real_decrease_still_triggers() -> None:
    """Eşik altında gerçek azalma (30000 → 28000) hâlâ alarm üretir."""
    readings = _make_readings([30000.0, 29500.0, 28000.0])
    message = await _check_counter(readings, seconds=300, tag_id="KWH_02")
    assert message is not None
    assert "Counter geri gitti" in message


@pytest.mark.asyncio
async def test_counter_small_decrease_below_high_threshold_triggers() -> None:
    """``first <= 60000`` ise rollover heuristic'i devreye girmez —
    örnek: 70 → 50 azalması gerçek arıza."""
    readings = _make_readings([70.0, 60.0, 50.0])
    message = await _check_counter(readings, seconds=300, tag_id="KWH_03")
    assert message is not None
    assert "Counter geri gitti" in message


@pytest.mark.asyncio
async def test_counter_stagnant_still_alarms() -> None:
    """Sayaç durağansa (pencere > seconds, değer eşit) alarm tetiklenir."""
    # 10 dakika boyunca aynı değer; eşik 300sn → durağan alarmı.
    readings = _make_readings(
        [42.0] * 11,
        spacing_seconds=60,
    )
    message = await _check_counter(readings, seconds=300, tag_id="KWH_04")
    assert message is not None
    assert "Counter durağan" in message


@pytest.mark.asyncio
async def test_counter_rollover_threshold_boundary() -> None:
    """Eşik tam sınırda (first=60000, last=5000) heuristic devreye
    girmemeli (strict greater/less than). Bu da gerçek geri-gitti."""
    readings = _make_readings([60000.0, 5000.0])
    message = await _check_counter(readings, seconds=300, tag_id="KWH_05")
    assert message is not None
    assert "Counter geri gitti" in message


def test_rollover_thresholds_are_sane() -> None:
    """Eşiklerin değerleri pencere içinde rollover sonrası en az bir kaç
    bin birim artışa izin vermeli; aksi takdirde rollover hemen sonrası
    okumaları gerçek azalma olarak yanlış sınıflar."""
    assert _COUNTER_ROLLOVER_HIGH_THRESHOLD > _COUNTER_ROLLOVER_LOW_THRESHOLD
    assert _COUNTER_ROLLOVER_HIGH_THRESHOLD < 65536  # uint16 max sınırının altı
    assert _COUNTER_ROLLOVER_LOW_THRESHOLD > 0
