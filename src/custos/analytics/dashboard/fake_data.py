"""Sahte veri üreteci — referans ekran için.

Deterministic sinüs+noise zaman serisi ve sahte alarm verileri üretir.
Her çağrıda aynı veriyi döndürür (reproducibility için).
"""

from __future__ import annotations

import math
import random
import time
from typing import Any


def _seeded_random(seed: int) -> random.Random:
    """Tekrarlanabilir rastgele sayı üreteci oluşturur."""
    return random.Random(seed)


def generate_time_series(
    sensor_id: str,
    points: int = 180,
    step_seconds: int = 10,
    base: float = 50.0,
    amplitude: float = 10.0,
    frequency: float = 0.05,
    noise: float = 1.0,
) -> dict[str, Any]:
    """Deterministic sinüs+noise zaman serisi üretir."""
    rng = _seeded_random(hash(sensor_id) % (2**32))
    # Güncel zamandan geriye doğru üret
    now = int(time.time())
    base_ts = now - (points * step_seconds)

    timestamps: list[int] = []
    values: list[float] = []

    for i in range(points):
        ts = base_ts + i * step_seconds
        val = base + amplitude * math.sin(i * frequency) + rng.uniform(-noise, noise)
        timestamps.append(ts)
        values.append(round(val, 2))

    return {"timestamps": timestamps, "values": values}


def get_overview_kpis() -> list[dict[str, str]]:
    """Overview sayfası için sahte KPI verileri döndürür."""
    return [
        {"label": "Active Alarms", "value": "2", "status": "warn", "delta": ""},
        {"label": "Total Sensors", "value": "24", "status": "neutral", "delta": ""},
        {"label": "Data Points / sec", "value": "24", "status": "ok", "delta": "+2.1"},
        {"label": "System Uptime", "value": "12d 4h", "status": "ok", "delta": ""},
    ]


def get_overview_charts() -> dict[str, Any]:
    """Overview sayfası için sahte grafik verileri döndürür."""
    # 360 nokta = 30 dakika x 5 saniyede 1 okuma — iyi çözünürlük, hızlı render
    pts = 360
    step = 5

    t1 = generate_time_series(
        "T001", points=pts, step_seconds=step,
        base=45, amplitude=10, frequency=0.005, noise=1.5,
    )
    t2 = generate_time_series(
        "T002", points=pts, step_seconds=step,
        base=55, amplitude=8, frequency=0.003, noise=2.0,
    )
    t3 = generate_time_series(
        "T003", points=pts, step_seconds=step,
        base=38, amplitude=5, frequency=0.007, noise=1.0,
    )

    p1 = generate_time_series(
        "P001", points=pts, step_seconds=step,
        base=5.5, amplitude=1.5, frequency=0.004, noise=0.3,
    )
    p2 = generate_time_series(
        "P002", points=pts, step_seconds=step,
        base=3.2, amplitude=0.8, frequency=0.006, noise=0.2,
    )

    # Overview grafiği — 1 saatlik (720 nokta, 5 saniyede 1)
    pts_1h = 720
    f1 = generate_time_series(
        "F001", points=pts_1h, step_seconds=step,
        base=250, amplitude=50, frequency=0.003, noise=10,
    )
    v1 = generate_time_series(
        "V001", points=pts_1h, step_seconds=step,
        base=12, amplitude=5, frequency=0.008, noise=1.5,
    )
    r1 = generate_time_series(
        "R001", points=pts_1h, step_seconds=step,
        base=1500, amplitude=200, frequency=0.002, noise=30,
    )

    return {
        "temp_chart": {
            "timestamps": t1["timestamps"],
            "series": [t1["values"], t2["values"], t3["values"]],
            "labels": ["T001 — Kazan Çıkış", "T002 — Dönüş", "T003 — Ortam"],
        },
        "pressure_chart": {
            "timestamps": p1["timestamps"],
            "series": [p1["values"], p2["values"]],
            "labels": ["P001 — Hat Basıncı", "P002 — Çıkış"],
        },
        "flow_vibration_chart": {
            "timestamps": f1["timestamps"],
            "series": [f1["values"], v1["values"]],
            "labels": ["F001 — Debi (m³/h)", "V001 — Titreşim (mm/s)"],
        },
        "rpm_chart": {
            "timestamps": r1["timestamps"],
            "series": [r1["values"]],
            "labels": ["R001 — Devir (RPM)"],
        },
    }


def get_recent_alarms() -> list[dict[str, str]]:
    """Sahte alarm tablosu verisi döndürür. Saf data, HTML yok."""
    return [
        {
            "time": "14:32:01", "sensor": "T001",
            "type": "High Temperature",
            "status": "crit", "status_label": "Critical",
        },
        {
            "time": "14:28:15", "sensor": "P001",
            "type": "Low Pressure",
            "status": "warn", "status_label": "Warning",
        },
        {
            "time": "14:15:42", "sensor": "V001",
            "type": "High Vibration",
            "status": "warn", "status_label": "Warning",
        },
        {
            "time": "13:52:30", "sensor": "T002",
            "type": "Sensor Offline",
            "status": "neutral", "status_label": "Resolved",
        },
        {
            "time": "13:41:18", "sensor": "R001",
            "type": "RPM Spike",
            "status": "ok", "status_label": "Normal",
        },
    ]
