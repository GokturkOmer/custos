"""AVM simülatörü için connection profile + 30 tag + 6 default proses chart.

Kullanım:
    docker compose up -d                 # TimescaleDB ayakta olmalı
    python -m custos.simulator           # ayrı terminalde, 5020 portu
    python scripts/seed_simulator_tags.py

Script idempotenttir: aynı tag_id'ler mevcutsa tekrar eklemez, mevcut
profile adı / chart_key varsa olanı kullanır. Default polling 1 saniye.

Pilot için gerçek cihaz tag'leri ayrı eklenir; bu script yalnız simülatör
için kısayoldur (Auto-Scan + manuel aktivasyon akışını atlamak için).
"""

from __future__ import annotations

import asyncio
import sys

import structlog

from custos.shared.config import settings
from custos.shared.database import (
    ConnectionProfile,
    OverviewChart,
    TagRecord,
    create_database,
)
from custos.shared.logging import configure_logging
from custos.simulator.sensors import SENSORS

logger = structlog.get_logger(logger_name="seed")

# Simülatör bağlantı bilgileri
SIM_PROFILE_NAME = "AVM Simülatör"
SIM_HOST = "127.0.0.1"
SIM_PORT = 5020
# 1 saniyelik polling — kullanıcı istediği hız
DEFAULT_POLLING_MS = 1000
DEFAULT_POLLING_PRESET = "normal"

# Varsayılan 6 proses chart'ı (AVM kataloğuyla eşleşen)
DEFAULT_CHARTS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("hvac-ahu", "HVAC / AHU",
     ("T001", "T002", "T003", "T004", "H001", "Q001")),
    ("chiller", "Chiller",
     ("T101", "T102", "T103", "T104", "P101", "I101")),
    ("kazan", "Kazan",
     ("T201", "T202", "T203", "P201", "F201")),
    ("pompa", "Pompa",
     ("P301", "P302", "I301", "V301", "F301")),
    ("elektrik", "Elektrik",
     ("E001", "E002", "E003", "E004")),
    ("sihhi-tesisat", "Sıhhi Tesisat",
     ("L001", "F401")),
)


async def _ensure_profile() -> ConnectionProfile:
    """Simülatör connection profile'ını oluşturur ya da varsa döndürür."""
    db = create_database(settings)
    await db.connect()
    try:
        existing = await db.list_connection_profiles()
        for p in existing:
            if p.name == SIM_PROFILE_NAME:
                await logger.ainfo(
                    "Connection profile mevcut, kullanılıyor",
                    profile=p.name, id=p.id,
                )
                return p
        new_profile = ConnectionProfile(
            name=SIM_PROFILE_NAME,
            host=SIM_HOST,
            port=SIM_PORT,
            unit_id_start=1,
            unit_id_end=1,
            status="idle",
        )
        saved = await db.insert_connection_profile(new_profile)
        await logger.ainfo(
            "Connection profile oluşturuldu",
            profile=saved.name, id=saved.id,
        )
        return saved
    finally:
        await db.close()


async def _seed_tags() -> tuple[int, int]:
    """Sensör kataloğundaki tag'leri DB'ye yazar. (eklendi, atlandı) döndürür."""
    db = create_database(settings)
    await db.connect()
    added = 0
    skipped = 0
    try:
        for sensor in SENSORS:
            existing = await db.get_tag(sensor.tag_id)
            if existing is not None:
                skipped += 1
                continue
            tag = TagRecord(
                tag_id=sensor.tag_id,
                name=sensor.name,
                modbus_host=SIM_HOST,
                modbus_port=SIM_PORT,
                unit_id=1,
                register_address=sensor.register,
                register_type="uint16",
                byte_order="big",
                gain=sensor.gain,
                offset=sensor.offset,
                unit=sensor.unit,
                polling_interval_ms=DEFAULT_POLLING_MS,
                polling_preset=DEFAULT_POLLING_PRESET,
                status="active",
            )
            await db.insert_tag(tag)
            added += 1
    finally:
        await db.close()
    return added, skipped


async def _seed_default_charts() -> tuple[int, int]:
    """6 default proses chart'ını DB'ye seedler. (eklendi, atlandı) döndürür."""
    db = create_database(settings)
    await db.connect()
    added = 0
    skipped = 0
    try:
        for idx, (chart_key, title, tag_ids) in enumerate(DEFAULT_CHARTS):
            existing = await db.get_overview_chart(chart_key)
            if existing is not None:
                skipped += 1
                continue
            await db.insert_overview_chart(OverviewChart(
                chart_key=chart_key, title=title, sort_order=idx,
            ))
            # Sadece simulator'da mevcut olan tag'leri bağla
            present_tags = [tid for tid in tag_ids if await db.get_tag(tid)]
            if present_tags:
                await db.replace_overview_chart_tags(
                    chart_key, list(present_tags),
                )
            added += 1
    finally:
        await db.close()
    return added, skipped


async def main() -> int:
    """Seed işlemini yürütür; eklenen/atlanan sayısını loglar."""
    configure_logging("INFO")
    await logger.ainfo(
        "AVM simülatör seed başlıyor",
        profile=SIM_PROFILE_NAME, host=SIM_HOST, port=SIM_PORT,
        sensör_sayısı=len(SENSORS),
        default_chart_sayısı=len(DEFAULT_CHARTS),
    )
    await _ensure_profile()
    tags_added, tags_skipped = await _seed_tags()
    charts_added, charts_skipped = await _seed_default_charts()
    await logger.ainfo(
        "Seed tamamlandı",
        tag_eklendi=tags_added, tag_atlandı=tags_skipped, tag_toplam=len(SENSORS),
        chart_eklendi=charts_added, chart_atlandı=charts_skipped,
        chart_toplam=len(DEFAULT_CHARTS),
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
