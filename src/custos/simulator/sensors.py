"""AVM pilotu için 30 sensörlük Modbus register haritası.

Register haritası (holding register, function code 0x03):
    0-7   HVAC/AHU        (sıcaklık, nem, CO2, taze hava)
    8-13  Chiller         (soğutma devreleri + kompresör)
    14-18 Kazan           (ısıtma devreleri + gaz akışı)
    19-23 Pompa           (basınç, akım, titreşim, debi)
    24-27 Elektrik        (güç, akım, gerilim, güç faktörü)
    28-29 Sıhhi tesisat   (su tankı seviyesi + kullanım suyu debisi)

Register değerleri uint16 olarak saklanır. Gerçek değer `register * gain`
ile elde edilir; ör. sıcaklık için gain=0.1, basınç için gain=0.01.

Pattern parametreleri saat bazlı değişim (diurnal + work hours boost)
üretir. Anomaliler ayrı bir katman olarak uygulanır.
"""

from __future__ import annotations

from dataclasses import dataclass

from custos.simulator.patterns import Anomaly, SensorPattern


@dataclass(frozen=True)
class SensorDef:
    """Tek bir sensörün register, dönüşüm ve pattern tanımı."""

    register: int  # holding register adresi (0-based)
    tag_id: str  # DB'deki tag_id (Auto-Scan önerisi)
    name: str  # kullanıcıya gösterilecek isim
    unit: str  # ölçü birimi
    gain: float  # register * gain = gerçek değer
    offset: float = 0.0  # register * gain + offset = gerçek değer
    pattern: SensorPattern = SensorPattern(base=0.0)
    anomaly: Anomaly | None = None


# AVM pilot sensör kataloğu
# Not: `base`, `diurnal_amp` ve anomali `delta` değerleri gerçek fiziksel
# birimde; `gain` ile register'a dönüştürülür.
SENSORS: tuple[SensorDef, ...] = (
    # --- HVAC / AHU ---
    SensorDef(
        register=0, tag_id="T001", name="Supply Air Temp", unit="°C", gain=0.1,
        pattern=SensorPattern(
            base=20.0, diurnal_amp=3.0, diurnal_peak_hour=15.0,
            workhours_boost=1.0, noise_amp=0.3,
            min_value=10.0, max_value=35.0,
        ),
    ),
    SensorDef(
        register=1, tag_id="T002", name="Return Air Temp", unit="°C", gain=0.1,
        pattern=SensorPattern(
            base=24.0, diurnal_amp=2.0, diurnal_peak_hour=15.0,
            workhours_boost=1.05, noise_amp=0.3,
            min_value=15.0, max_value=35.0,
        ),
    ),
    SensorDef(
        register=2, tag_id="T003", name="Outdoor Air Temp", unit="°C", gain=0.1,
        pattern=SensorPattern(
            base=16.0, diurnal_amp=8.0, diurnal_peak_hour=14.0,
            noise_amp=0.5, min_value=-5.0, max_value=40.0,
        ),
    ),
    SensorDef(
        register=3, tag_id="T004", name="Mixed Air Temp", unit="°C", gain=0.1,
        pattern=SensorPattern(
            base=22.0, diurnal_amp=2.5, diurnal_peak_hour=15.0,
            workhours_boost=1.02, noise_amp=0.3,
            min_value=10.0, max_value=32.0,
        ),
    ),
    SensorDef(
        register=4, tag_id="H001", name="Indoor Humidity", unit="%", gain=0.1,
        pattern=SensorPattern(
            base=50.0, diurnal_amp=5.0, diurnal_peak_hour=6.0,
            noise_amp=1.0, min_value=20.0, max_value=90.0,
        ),
    ),
    SensorDef(
        register=5, tag_id="H002", name="Outdoor Humidity", unit="%", gain=0.1,
        pattern=SensorPattern(
            base=65.0, diurnal_amp=15.0, diurnal_peak_hour=4.0,
            noise_amp=2.0, min_value=15.0, max_value=100.0,
        ),
    ),
    SensorDef(
        register=6, tag_id="Q001", name="Indoor CO2", unit="ppm", gain=1.0,
        pattern=SensorPattern(
            base=450.0, diurnal_amp=80.0, diurnal_peak_hour=13.0,
            workhours_boost=1.4, workhours_only=True, noise_amp=20.0,
            min_value=400.0, max_value=2000.0,
        ),
        anomaly=Anomaly(
            kind="daily_multi_peak", delta=350.0,
            hours=(13, 19), duration_minutes=30,
        ),
    ),
    SensorDef(
        register=7, tag_id="F001", name="Fresh Air Flow", unit="m³/h", gain=1.0,
        pattern=SensorPattern(
            base=2000.0, diurnal_amp=400.0, diurnal_peak_hour=15.0,
            workhours_boost=1.3, workhours_only=True, noise_amp=40.0,
            min_value=100.0, max_value=5000.0,
        ),
    ),
    # --- Chiller ---
    SensorDef(
        register=8, tag_id="T101", name="Chilled Water Supply", unit="°C", gain=0.1,
        pattern=SensorPattern(
            base=7.0, diurnal_amp=0.5, diurnal_peak_hour=15.0,
            noise_amp=0.1, min_value=4.0, max_value=12.0,
        ),
    ),
    SensorDef(
        register=9, tag_id="T102", name="Chilled Water Return", unit="°C", gain=0.1,
        pattern=SensorPattern(
            base=12.0, diurnal_amp=1.5, diurnal_peak_hour=15.0,
            workhours_boost=1.1, noise_amp=0.2,
            min_value=8.0, max_value=20.0,
        ),
    ),
    SensorDef(
        register=10, tag_id="T103", name="Condenser In", unit="°C", gain=0.1,
        pattern=SensorPattern(
            base=30.0, diurnal_amp=2.0, diurnal_peak_hour=15.0,
            workhours_boost=1.05, noise_amp=0.3,
            min_value=20.0, max_value=50.0,
        ),
        anomaly=Anomaly(
            kind="daily_spike", delta=8.0,
            hours=(14,), duration_minutes=10,
        ),
    ),
    SensorDef(
        register=11, tag_id="T104", name="Condenser Out", unit="°C", gain=0.1,
        pattern=SensorPattern(
            base=35.0, diurnal_amp=2.0, diurnal_peak_hour=15.0,
            workhours_boost=1.05, noise_amp=0.3,
            min_value=25.0, max_value=55.0,
        ),
    ),
    SensorDef(
        register=12, tag_id="P101", name="Refrigerant Pressure", unit="bar", gain=0.01,
        pattern=SensorPattern(
            base=6.5, diurnal_amp=0.5, diurnal_peak_hour=15.0,
            workhours_boost=1.05, noise_amp=0.1,
            min_value=4.0, max_value=12.0,
        ),
    ),
    SensorDef(
        register=13, tag_id="I101", name="Compressor Current", unit="A", gain=0.1,
        pattern=SensorPattern(
            base=110.0, diurnal_amp=20.0, diurnal_peak_hour=15.0,
            workhours_boost=1.2, workhours_only=True, noise_amp=3.0,
            min_value=40.0, max_value=200.0,
        ),
    ),
    # --- Kazan ---
    SensorDef(
        register=14, tag_id="T201", name="Boiler Supply", unit="°C", gain=0.1,
        pattern=SensorPattern(
            base=70.0, diurnal_amp=5.0, diurnal_peak_hour=7.0,
            workhours_boost=1.1, noise_amp=0.5,
            min_value=55.0, max_value=90.0,
        ),
    ),
    SensorDef(
        register=15, tag_id="T202", name="Boiler Return", unit="°C", gain=0.1,
        pattern=SensorPattern(
            base=50.0, diurnal_amp=3.0, diurnal_peak_hour=7.0,
            workhours_boost=1.05, noise_amp=0.5,
            min_value=35.0, max_value=70.0,
        ),
    ),
    SensorDef(
        register=16, tag_id="P201", name="Boiler Pressure", unit="bar", gain=0.01,
        pattern=SensorPattern(
            base=2.0, diurnal_amp=0.2, diurnal_peak_hour=7.0,
            workhours_boost=1.05, noise_amp=0.05,
            min_value=1.0, max_value=4.0,
        ),
    ),
    SensorDef(
        register=17, tag_id="F201", name="Gas Flow", unit="m³/h", gain=0.1,
        pattern=SensorPattern(
            base=35.0, diurnal_amp=10.0, diurnal_peak_hour=7.0,
            workhours_boost=1.3, workhours_only=True, noise_amp=1.0,
            min_value=5.0, max_value=80.0,
        ),
    ),
    SensorDef(
        register=18, tag_id="T203", name="Flue Gas Temp", unit="°C", gain=0.1,
        pattern=SensorPattern(
            base=180.0, diurnal_amp=20.0, diurnal_peak_hour=7.0,
            workhours_boost=1.1, noise_amp=2.0,
            min_value=80.0, max_value=300.0,
        ),
    ),
    # --- Pompa ---
    SensorDef(
        register=19, tag_id="P301", name="Pump Discharge Pressure", unit="bar", gain=0.01,
        pattern=SensorPattern(
            base=4.0, diurnal_amp=0.2, diurnal_peak_hour=12.0,
            workhours_boost=1.02, noise_amp=0.05,
            min_value=2.0, max_value=8.0,
        ),
    ),
    SensorDef(
        register=20, tag_id="P302", name="Pump Suction Pressure", unit="bar", gain=0.01,
        pattern=SensorPattern(
            base=1.0, diurnal_amp=0.15, diurnal_peak_hour=12.0,
            workhours_boost=0.98, noise_amp=0.05,
            min_value=0.2, max_value=2.5,
        ),
    ),
    SensorDef(
        register=21, tag_id="I301", name="Pump Current", unit="A", gain=0.1,
        pattern=SensorPattern(
            base=11.0, diurnal_amp=1.0, diurnal_peak_hour=12.0,
            noise_amp=0.2, min_value=5.0, max_value=25.0,
        ),
        anomaly=Anomaly(
            kind="daily_spike", delta=10.0,
            hours=(8,), duration_minutes=3,
        ),
    ),
    SensorDef(
        register=22, tag_id="V301", name="Pump Vibration", unit="mm/s", gain=0.1,
        pattern=SensorPattern(
            base=3.0, diurnal_amp=0.3, diurnal_peak_hour=12.0,
            noise_amp=0.1, min_value=1.0, max_value=15.0,
        ),
        anomaly=Anomaly(
            kind="wear_trend", delta=4.0,
        ),
    ),
    SensorDef(
        register=23, tag_id="F301", name="Circulation Flow", unit="L/min", gain=1.0,
        pattern=SensorPattern(
            base=300.0, diurnal_amp=50.0, diurnal_peak_hour=15.0,
            workhours_boost=1.1, workhours_only=True, noise_amp=5.0,
            min_value=50.0, max_value=600.0,
        ),
    ),
    # --- Elektrik ---
    SensorDef(
        register=24, tag_id="E001", name="Total Power", unit="kW", gain=1.0,
        pattern=SensorPattern(
            base=400.0, diurnal_amp=150.0, diurnal_peak_hour=15.0,
            workhours_boost=1.5, workhours_only=True, noise_amp=10.0,
            min_value=100.0, max_value=1200.0,
        ),
        anomaly=Anomaly(
            kind="weekly_dropout", delta=-300.0,
            hours=(11,), duration_minutes=5, weekday=2,  # Çarşamba
        ),
    ),
    SensorDef(
        register=25, tag_id="E002", name="Main Current L1", unit="A", gain=1.0,
        pattern=SensorPattern(
            base=500.0, diurnal_amp=200.0, diurnal_peak_hour=15.0,
            workhours_boost=1.5, workhours_only=True, noise_amp=15.0,
            min_value=100.0, max_value=1500.0,
        ),
    ),
    SensorDef(
        register=26, tag_id="E003", name="Main Voltage", unit="V", gain=1.0,
        pattern=SensorPattern(
            base=220.0, diurnal_amp=3.0, diurnal_peak_hour=18.0,
            noise_amp=1.0, min_value=200.0, max_value=240.0,
        ),
    ),
    SensorDef(
        register=27, tag_id="E004", name="Power Factor", unit="%", gain=0.01,
        pattern=SensorPattern(
            base=88.0, diurnal_amp=3.0, diurnal_peak_hour=15.0,
            workhours_boost=1.02, noise_amp=0.5,
            min_value=70.0, max_value=100.0,
        ),
    ),
    # --- Sıhhi tesisat ---
    SensorDef(
        register=28, tag_id="L001", name="Water Tank Level", unit="%", gain=0.1,
        pattern=SensorPattern(
            base=70.0, diurnal_amp=15.0, diurnal_peak_hour=4.0,
            noise_amp=1.0, min_value=20.0, max_value=100.0,
        ),
    ),
    SensorDef(
        register=29, tag_id="F401", name="Domestic Water Flow", unit="L/min", gain=0.1,
        pattern=SensorPattern(
            base=80.0, diurnal_amp=30.0, diurnal_peak_hour=15.0,
            workhours_boost=1.4, workhours_only=True, noise_amp=3.0,
            min_value=0.0, max_value=300.0,
        ),
    ),
)


def sensor_count() -> int:
    """Tanımlı sensör sayısı."""
    return len(SENSORS)


def max_register() -> int:
    """En yüksek register adresi (register map boyutunu belirler)."""
    return max(s.register for s in SENSORS)
