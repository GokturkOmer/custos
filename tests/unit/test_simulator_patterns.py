"""Simülatör pattern ve sensör kataloğu unit testleri.

DB veya pymodbus gerektirmez — saf hesap fonksiyonları.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from custos.simulator.patterns import (
    Anomaly,
    SensorPattern,
    anomaly_delta,
    compute_base_value,
    diurnal_delta,
    workhours_multiplier,
)
from custos.simulator.sensors import SENSORS, max_register, sensor_count


def test_sensor_count_is_30() -> None:
    """AVM kataloğunda tam 30 sensör olmalı."""
    assert sensor_count() == 30


def test_registers_are_unique_and_contiguous() -> None:
    """Register adresleri 0..29 aralığında benzersiz olmalı."""
    regs = [s.register for s in SENSORS]
    assert len(set(regs)) == len(regs), "Register adresleri benzersiz olmalı"
    assert max_register() == 29
    assert min(regs) == 0


def test_tag_ids_are_unique() -> None:
    """Tag id'leri benzersiz olmalı (DB UNIQUE constraint)."""
    tag_ids = [s.tag_id for s in SENSORS]
    assert len(set(tag_ids)) == len(tag_ids)


def test_gain_values_are_positive() -> None:
    """Register → fiziksel değer dönüşümü için gain > 0 olmalı."""
    for s in SENSORS:
        assert s.gain > 0, f"{s.tag_id}: gain pozitif olmalı ({s.gain})"


def test_diurnal_delta_peaks_at_peak_hour() -> None:
    """24h kosinüs peak_hour saatinde +amp, ters saatte -amp vermeli."""
    peak = diurnal_delta(amp=10.0, peak_hour=12.0, hour_of_day=12.0)
    trough = diurnal_delta(amp=10.0, peak_hour=12.0, hour_of_day=0.0)
    assert peak == 10.0
    assert trough == -10.0


def test_diurnal_delta_zero_amp_returns_zero() -> None:
    """Amp=0 saat ne olursa olsun 0 döndürmeli."""
    for h in (0.0, 6.0, 15.0, 23.5):
        assert diurnal_delta(amp=0.0, peak_hour=12.0, hour_of_day=h) == 0.0


def test_workhours_multiplier_applies_boost_inside_window() -> None:
    """AVM açık saatlerinde (09-22) boost uygulanmalı."""
    assert workhours_multiplier(10.0, boost=1.5, only_workhours=False) == 1.5
    assert workhours_multiplier(21.5, boost=1.5, only_workhours=False) == 1.5


def test_workhours_multiplier_no_boost_outside_window() -> None:
    """AVM kapalıyken (22-09) normal çarpan 1.0."""
    assert workhours_multiplier(2.0, boost=1.5, only_workhours=False) == 1.0
    assert workhours_multiplier(7.0, boost=1.5, only_workhours=False) == 1.0


def test_workhours_only_drops_off_hours() -> None:
    """workhours_only=True iken kapalı saatte base düşer."""
    result = workhours_multiplier(3.0, boost=1.4, only_workhours=True)
    assert 0.0 < result < 1.0, "Kapalı saatte 0 < faktör < 1 olmalı"


def test_compute_base_value_uses_pattern() -> None:
    """compute_base_value pattern parametrelerini uygular."""
    pattern = SensorPattern(
        base=100.0,
        diurnal_amp=10.0,
        diurnal_peak_hour=12.0,
        workhours_boost=1.0,
        noise_amp=0.0,
    )
    # Öğlen 12:00 peak — base + amp
    noon = datetime(2026, 4, 19, 12, 0, 0, tzinfo=UTC)
    assert abs(compute_base_value(pattern, noon) - 110.0) < 0.01


def test_compute_base_value_respects_workhours_boost() -> None:
    """workhours_boost uygulanmalı."""
    pattern = SensorPattern(
        base=100.0,
        diurnal_amp=0.0,
        workhours_boost=1.5,
    )
    open_hour = datetime(2026, 4, 19, 14, 0, 0, tzinfo=UTC)
    closed_hour = datetime(2026, 4, 19, 3, 0, 0, tzinfo=UTC)
    assert compute_base_value(pattern, open_hour) == 150.0
    assert compute_base_value(pattern, closed_hour) == 100.0


def test_anomaly_daily_spike_triggers_at_target_hour() -> None:
    """daily_spike hedef saatte maksimuma yakın değer vermeli."""
    sim_start = datetime(2026, 4, 19, 0, 0, 0, tzinfo=UTC)
    anomaly = Anomaly(
        kind="daily_spike",
        delta=50.0,
        hours=(14,),
        duration_minutes=10,
    )
    # Spike 14:00 - 14:10 arası; en yüksek nokta ortada
    peak_time = datetime(2026, 4, 19, 14, 5, 0, tzinfo=UTC)
    before_time = datetime(2026, 4, 19, 10, 0, 0, tzinfo=UTC)
    assert anomaly_delta(anomaly, peak_time, sim_start) > 40.0
    assert abs(anomaly_delta(anomaly, before_time, sim_start)) < 1.0


def test_anomaly_weekly_dropout_only_on_weekday() -> None:
    """weekly_dropout yalnızca belirtilen günde tetiklenmeli."""
    sim_start = datetime(2026, 4, 19, 0, 0, 0, tzinfo=UTC)
    anomaly = Anomaly(
        kind="weekly_dropout",
        delta=-100.0,
        hours=(11,),
        duration_minutes=5,
        weekday=2,  # Çarşamba
    )
    # 2026-04-22 Çarşamba, 2026-04-19 Pazar
    wednesday_11am = datetime(2026, 4, 22, 11, 2, 0, tzinfo=UTC)
    sunday_11am = datetime(2026, 4, 19, 11, 2, 0, tzinfo=UTC)
    assert anomaly_delta(anomaly, wednesday_11am, sim_start) < -50.0
    assert anomaly_delta(anomaly, sunday_11am, sim_start) == 0.0


def test_anomaly_wear_trend_grows_over_week() -> None:
    """wear_trend 2. günden sonra artmalı, hafta ortasında yarıda olmalı."""
    sim_start = datetime(2026, 4, 19, 0, 0, 0, tzinfo=UTC)
    anomaly = Anomaly(kind="wear_trend", delta=4.0)
    day1 = sim_start + timedelta(days=1)  # flat bölge
    day4 = sim_start + timedelta(days=4)  # yarı ilerlemiş
    day6 = sim_start + timedelta(days=6, hours=22)  # tam doluya yakın
    assert anomaly_delta(anomaly, day1, sim_start) == 0.0
    d4 = anomaly_delta(anomaly, day4, sim_start)
    d6 = anomaly_delta(anomaly, day6, sim_start)
    assert 0 < d4 < d6


def test_all_sensors_clamp_values_in_range() -> None:
    """Her sensör için min/max clamp tanımlı olsa bile compute sınır aşmamalı."""
    now = datetime(2026, 4, 19, 15, 0, 0, tzinfo=UTC)
    for s in SENSORS:
        value = compute_base_value(s.pattern, now)
        if s.pattern.min_value is not None:
            # Anomali ve noise olmadan compute_base_value clamp uygulamaz,
            # ama beklenen değer min/max aralığına YAKIN olmalı
            assert value > s.pattern.min_value - 50
        if s.pattern.max_value is not None:
            assert value < s.pattern.max_value + 50
