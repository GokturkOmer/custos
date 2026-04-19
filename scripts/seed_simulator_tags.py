"""AVM simülatörü için connection profile + 30 tag'i DB'ye seed eder.

Kullanım:
    docker compose up -d                 # TimescaleDB ayakta olmalı
    python -m custos.simulator           # ayrı terminalde, 5020 portu
    python scripts/seed_simulator_tags.py

Script idempotenttir: aynı tag_id'ler mevcutsa tekrar eklemez, mevcut
profile adı varsa onu kullanır. Default polling 1 saniye (Normal preset).

Pilot için gerçek cihaz tag'leri ayrı eklenir; bu script yalnız simülatör
için kısayoldur (Auto-Scan + manuel aktivasyon akışını atlamak için).
"""

from __future__ import annotations

import asyncio
import sys

import structlog

from custos.shared.config import settings
from custos.shared.database import ConnectionProfile, TagRecord, create_database
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


async def main() -> int:
    """Seed işlemini yürütür; eklenen/atlanan sayısını loglar."""
    configure_logging("INFO")
    await logger.ainfo(
        "AVM simülatör seed başlıyor",
        profile=SIM_PROFILE_NAME, host=SIM_HOST, port=SIM_PORT,
        sensör_sayısı=len(SENSORS),
    )
    await _ensure_profile()
    added, skipped = await _seed_tags()
    await logger.ainfo(
        "Seed tamamlandı",
        eklendi=added, atlandı=skipped, toplam=len(SENSORS),
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
