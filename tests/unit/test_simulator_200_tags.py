"""Endurance 200 tag katalog + env-aware active_sensors seçim testleri.

Bu testler DB veya pymodbus server gerektirmez — saf katalog + pattern
doğrulaması. Amaç: endurance yükünde simülatörün 200 register ile
uint16 sınırları içinde kaldığını + monotonic sayacın saat başına delta
kadar arttığını + env değişkeninin doğru kataloğu seçtiğini garanti etmek.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from custos.simulator.modbus_server import compute_sensor_register
from custos.simulator.patterns import Anomaly, anomaly_delta
from custos.simulator.sensors import (
    SENSORS,
    active_sensors,
    build_endurance_sensors,
)


def test_endurance_catalog_has_200_sensors() -> None:
    """Endurance kataloğu tam 200 sensör üretmeli."""
    sensors = build_endurance_sensors()
    assert len(sensors) == 200


def test_endurance_tag_ids_are_t001_to_t200() -> None:
    """Tag_id'ler 'T001'..'T200' formatında ve benzersiz olmalı."""
    sensors = build_endurance_sensors()
    tag_ids = [s.tag_id for s in sensors]
    assert tag_ids[0] == "T001"
    assert tag_ids[49] == "T050"
    assert tag_ids[199] == "T200"
    assert len(set(tag_ids)) == 200


def test_endurance_registers_are_0_to_199() -> None:
    """Register adresleri 0-199 aralığında contiguous olmalı."""
    sensors = build_endurance_sensors()
    registers = [s.register for s in sensors]
    assert registers == list(range(200))


def test_endurance_section_distribution() -> None:
    """Bölüm dağılımı 50/50/50/30/20 olmalı.

    Birim (unit) alanından bölüm çıkarımı:
    - °C   : sıcaklık (50)
    - bar  : basınç (50)
    - kWh  : enerji (50)
    - rpm  : RPM (30)
    - ""   : durum biti (20)
    """
    sensors = build_endurance_sensors()
    units = [s.unit for s in sensors]
    assert units.count("°C") == 50
    assert units.count("bar") == 50
    assert units.count("kWh") == 50
    assert units.count("rpm") == 30
    assert units.count("") == 20


def test_endurance_energy_sensors_are_monotonic() -> None:
    """Enerji sensörlerinde anomaly kind='monotonic' ve delta>0 olmalı."""
    sensors = build_endurance_sensors()
    energy = [s for s in sensors if s.unit == "kWh"]
    assert len(energy) == 50
    for s in energy:
        assert s.anomaly is not None, f"{s.tag_id} enerji ama monotonic anomaly yok"
        assert s.anomaly.kind == "monotonic"
        assert s.anomaly.delta > 0


def test_monotonic_anomaly_increments_per_hour() -> None:
    """monotonic kind anomaly_delta: sim_start + N saat → delta * N üretmeli."""
    sim_start = datetime(2026, 4, 22, 12, 0, 0, tzinfo=UTC)
    anomaly = Anomaly(kind="monotonic", delta=5.0)

    assert anomaly_delta(anomaly, sim_start, sim_start) == 0.0
    one_hour = sim_start + timedelta(hours=1)
    assert anomaly_delta(anomaly, one_hour, sim_start) == pytest.approx(5.0)
    ten_hours = sim_start + timedelta(hours=10)
    assert anomaly_delta(anomaly, ten_hours, sim_start) == pytest.approx(50.0)


def test_monotonic_anomaly_before_sim_start_returns_zero() -> None:
    """sim_start'tan önceki 'now' için monotonic 0 döner (negatif çıkmaz)."""
    sim_start = datetime(2026, 4, 22, 12, 0, 0, tzinfo=UTC)
    earlier = sim_start - timedelta(hours=2)
    anomaly = Anomaly(kind="monotonic", delta=5.0)
    assert anomaly_delta(anomaly, earlier, sim_start) == 0.0


def test_endurance_register_values_fit_uint16() -> None:
    """Tüm endurance sensörleri 7 gün boyunca uint16 sınırlarında kalmalı."""
    sensors = build_endurance_sensors()
    sim_start = datetime(2026, 4, 22, 0, 0, 0, tzinfo=UTC)
    # Dört ayrı anda örnekle: start, 2 gün, 5 gün, 7 gün
    sample_times = [
        sim_start,
        sim_start + timedelta(days=2),
        sim_start + timedelta(days=5),
        sim_start + timedelta(days=7),
    ]
    for now in sample_times:
        for sensor in sensors:
            reg = compute_sensor_register(sensor, now, sim_start, noise=0.0)
            assert 0 <= reg <= 65535, (
                f"{sensor.tag_id} @ {now.isoformat()} register {reg} uint16 dışında"
            )


def test_active_sensors_default_is_avm_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    """Env yoksa AVM kataloğu (30) aktif olmalı."""
    monkeypatch.delenv("CUSTOS_TAG_COUNT", raising=False)
    sensors = active_sensors()
    assert sensors is SENSORS
    assert len(sensors) == 30


def test_active_sensors_with_200_selects_endurance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CUSTOS_TAG_COUNT=200 → endurance kataloğu (200 sensör)."""
    monkeypatch.setenv("CUSTOS_TAG_COUNT", "200")
    sensors = active_sensors()
    assert len(sensors) == 200
    assert sensors[0].tag_id == "T001"
    assert sensors[-1].tag_id == "T200"


def test_active_sensors_unknown_value_falls_back_to_avm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tanımsız değer (ör. '30' veya 'foo') → AVM kataloğu (safe default)."""
    monkeypatch.setenv("CUSTOS_TAG_COUNT", "30")
    assert active_sensors() is SENSORS
    monkeypatch.setenv("CUSTOS_TAG_COUNT", "garbage")
    assert active_sensors() is SENSORS
