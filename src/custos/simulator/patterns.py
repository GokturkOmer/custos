"""Simülatör sensör pattern motoru.

Her sensörün değerini zamana göre üretir:
- 24 saatlik sinüs tabanlı diurnal değişim (gündüz/gece farkı)
- AVM açık saatlerinde (09–22) ek çarpan
- Opsiyonel kısa vadeli gürültü
- Zamanlı anomaliler (spike, dropout, trend)

Bu modül pymodbus bağımsızdır, saf hesap yapar.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

# AVM açık saatleri (gündüz mal sahibi/çalışan aktif)
WORKHOURS_START = 9
WORKHOURS_END = 22
# AVM kapalıyken baz değerin düştüğü oran (ör. debi, CO2, enerji)
OFFHOURS_FACTOR = 0.35


@dataclass(frozen=True)
class SensorPattern:
    """Bir sensörün zaman bazlı değer üretim parametreleri.

    Tüm değerler gerçek fiziksel birimdedir (register dönüşümü ayrı yapılır).
    """

    base: float  # gün ortalaması
    diurnal_amp: float = 0.0  # 24h sinüs genliği (0 ise sabit)
    diurnal_peak_hour: float = 15.0  # gündüz hangi saatte maksimum
    workhours_boost: float = 1.0  # açık saatlerde base * boost
    workhours_only: bool = False  # True: kapalıyken OFFHOURS_FACTOR ile düşer
    noise_amp: float = 0.0  # her tick eklenen gaussian gürültü
    min_value: float | None = None  # alt clamp
    max_value: float | None = None  # üst clamp


@dataclass(frozen=True)
class Anomaly:
    """Zamanlı anomali tanımı.

    Tek bir tag'e atanır, pattern çıktısına delta olarak eklenir.
    """

    # Kind kümesi:
    #   "daily_spike"      — her gün belirli saatlerde gaussian spike
    #   "daily_multi_peak" — aynı spike mantığı, birden fazla saat
    #   "weekly_dropout"   — haftanın belirli gününde dropout
    #   "wear_trend"       — 7 günde sıfırdan delta'ya yükselir, cyclic
    #   "monotonic"        — sim_start'tan itibaren saatte `delta` birim artış
    #                        (enerji sayacı / kWh gibi sürekli sayaçlar için)
    kind: str
    delta: float  # etki miktarı (pozitif = artış, negatif = dropout)
    hours: tuple[int, ...] = field(default_factory=tuple)  # günlük tetik saatleri
    duration_minutes: int = 10  # spike/dropout süresi
    weekday: int = 0  # haftalık anomaliler için (0=Pazartesi)


def _hour_of_day(now: datetime) -> float:
    """Günün kaçıncı saati (0.0-24.0) — alt-saat hassasiyetinde."""
    return now.hour + now.minute / 60.0 + now.second / 3600.0


def diurnal_delta(amp: float, peak_hour: float, hour_of_day: float) -> float:
    """24 saatlik kosinüs: peak_hour'da +amp, ters saatte -amp."""
    if amp == 0.0:
        return 0.0
    phase = (hour_of_day - peak_hour) * (2 * math.pi / 24)
    return amp * math.cos(phase)


def workhours_multiplier(
    hour_of_day: float,
    boost: float,
    only_workhours: bool,
) -> float:
    """AVM açık/kapalı saatlerinde çarpan."""
    open_now = WORKHOURS_START <= hour_of_day < WORKHOURS_END
    if open_now:
        return boost
    if only_workhours:
        return OFFHOURS_FACTOR
    return 1.0


def compute_base_value(pattern: SensorPattern, now: datetime) -> float:
    """Anomali ve gürültü hariç anlık değer.

    Gürültü ve anomaliler ayrıca eklenir (testlenebilirlik için).
    """
    hour = _hour_of_day(now)
    value = pattern.base + diurnal_delta(
        pattern.diurnal_amp, pattern.diurnal_peak_hour, hour
    )
    value *= workhours_multiplier(
        hour, pattern.workhours_boost, pattern.workhours_only
    )
    return value


def _gaussian_window(hour_of_day: float, center: float, duration_min: int) -> float:
    """Merkezi center'da olan gaussian penceresi (0-1 arası).

    Pencere duration_min'in yaklaşık yarısında 0'a düşer.
    """
    half_width_h = (duration_min / 60.0) / 2
    if half_width_h <= 0:
        return 0.0
    sigma = half_width_h / 2  # 2σ ≈ yarı genişlik
    delta = hour_of_day - center
    return math.exp(-(delta * delta) / (2 * sigma * sigma))


def anomaly_delta(
    anomaly: Anomaly,
    now: datetime,
    sim_start: datetime,
) -> float:
    """Bir anomalinin şu anki katkısı. Etkili değilse 0."""
    hour = _hour_of_day(now)

    if anomaly.kind == "daily_spike":
        # Her gün belirli saatlerde gaussian spike
        best = 0.0
        for target_hour in anomaly.hours:
            mid = target_hour + (anomaly.duration_minutes / 60.0) / 2
            factor = _gaussian_window(hour, mid, anomaly.duration_minutes)
            if factor > best:
                best = factor
        return anomaly.delta * best

    if anomaly.kind == "daily_multi_peak":
        # Aynı spike mantığı — semantik vurgu için ayrı kind
        best = 0.0
        for target_hour in anomaly.hours:
            mid = target_hour + (anomaly.duration_minutes / 60.0) / 2
            factor = _gaussian_window(hour, mid, anomaly.duration_minutes)
            if factor > best:
                best = factor
        return anomaly.delta * best

    if anomaly.kind == "weekly_dropout":
        # Haftanın belirli günü, belirli saatte dropout
        if now.weekday() != anomaly.weekday:
            return 0.0
        best = 0.0
        for target_hour in anomaly.hours:
            mid = target_hour + (anomaly.duration_minutes / 60.0) / 2
            factor = _gaussian_window(hour, mid, anomaly.duration_minutes)
            if factor > best:
                best = factor
        return anomaly.delta * best

    if anomaly.kind == "wear_trend":
        # Haftalık yavaşça artan aşınma; 7 günde sıfırdan delta'ya yükselir
        days_elapsed = (now - sim_start).total_seconds() / 86400.0
        weekly_phase = days_elapsed % 7.0
        # ilk 2 gün flat, 2-7 arası lineer artış
        if weekly_phase < 2.0:
            return 0.0
        progress = (weekly_phase - 2.0) / 5.0  # 0..1
        return anomaly.delta * progress

    if anomaly.kind == "monotonic":
        # Sim_start'tan itibaren geçen saat başına `delta` birim ekler.
        # Enerji sayacı / toplam kWh gibi sürekli artan sayaçlar için kullanılır.
        hours_elapsed = (now - sim_start).total_seconds() / 3600.0
        if hours_elapsed < 0.0:
            return 0.0
        return anomaly.delta * hours_elapsed

    return 0.0
