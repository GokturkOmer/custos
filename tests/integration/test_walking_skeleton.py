"""Walking skeleton uçtan uca entegrasyon testi.

Simülatör → Collector → TimescaleDB zincirini doğrular.
TimescaleDB'nin ayakta olmasını gerektirir (docker compose up -d).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from custos.critical.collector import ModbusCollector
from custos.shared.config import Settings
from custos.shared.database import TagRecord, TimescaleDBDatabase
from custos.shared.logging import configure_logging
from custos.simulator.modbus_server import ModbusSimulator

# AVM sensör kataloğundan ilk 5 tag (register 0-4) — walking skeleton kapsamı.
# Değer aralıkları pattern clamp'leriyle eşleşir; gain uygulanmış gerçek değerde.
# tag_id'ler WS_ ile prefixli: makinede paralel custos.critical çalışıyorsa
# üretim T001 vb. kayıtlarıyla karışmaz, sorgu/cleanup yalnız bu test'e aittir.
TAG_RANGES: dict[str, tuple[float, float]] = {
    "WS_T001": (5.0, 40.0),  # Supply Air Temp °C
    "WS_T002": (10.0, 40.0),  # Return Air Temp °C
    "WS_T003": (-10.0, 45.0),  # Outdoor Air Temp °C
    "WS_T004": (5.0, 35.0),  # Mixed Air Temp °C
    "WS_H001": (15.0, 95.0),  # Indoor Humidity %
}

# Test tag'leri — simülatörün ilk 5 register adresi
# polling_interval_ms=1000, simulator update 500ms ile 5 saniyede ~5 okuma beklenir
TEST_TAGS: list[TagRecord] = [
    TagRecord(
        tag_id="WS_T001",
        name="WS Supply Air Temp",
        modbus_host="127.0.0.1",
        modbus_port=5030,
        unit_id=1,
        register_address=0,
        register_type="uint16",
        gain=0.1,
        offset=0.0,
        unit="°C",
        polling_interval_ms=1000,
        polling_preset="normal",
    ),
    TagRecord(
        tag_id="WS_T002",
        name="WS Return Air Temp",
        modbus_host="127.0.0.1",
        modbus_port=5030,
        unit_id=1,
        register_address=1,
        register_type="uint16",
        gain=0.1,
        offset=0.0,
        unit="°C",
        polling_interval_ms=1000,
        polling_preset="normal",
    ),
    TagRecord(
        tag_id="WS_T003",
        name="WS Outdoor Air Temp",
        modbus_host="127.0.0.1",
        modbus_port=5030,
        unit_id=1,
        register_address=2,
        register_type="uint16",
        gain=0.1,
        offset=0.0,
        unit="°C",
        polling_interval_ms=1000,
        polling_preset="normal",
    ),
    TagRecord(
        tag_id="WS_T004",
        name="WS Mixed Air Temp",
        modbus_host="127.0.0.1",
        modbus_port=5030,
        unit_id=1,
        register_address=3,
        register_type="uint16",
        gain=0.1,
        offset=0.0,
        unit="°C",
        polling_interval_ms=1000,
        polling_preset="normal",
    ),
    TagRecord(
        tag_id="WS_H001",
        name="WS Indoor Humidity",
        modbus_host="127.0.0.1",
        modbus_port=5030,
        unit_id=1,
        register_address=4,
        register_type="uint16",
        gain=0.1,
        offset=0.0,
        unit="%",
        polling_interval_ms=1000,
        polling_preset="normal",
    ),
]


@pytest.fixture(autouse=True)
def _setup_logging() -> None:
    """Test için loglama yapılandırması."""
    configure_logging("INFO")


async def _db_is_available(db: TimescaleDBDatabase) -> bool:
    """Veritabanının erişilebilir olup olmadığını kontrol eder."""
    try:
        await db.connect()
        result = await db.health_check()
        await db.close()
    except Exception:
        return False
    else:
        return result


async def test_walking_skeleton() -> None:
    """Simülatör + Collector + DB uçtan uca çalışıyor mu?

    1. DB ayakta mı kontrol et
    2. Yalnız bu test'in WS_* okumalarını temizle (üretim verisine dokunma)
    3. Simülatörü başlat
    4. 2 saniye bekle (warm-up)
    5. Collector'ı 5 saniye çalıştır
    6. Durdur
    7. Her tag'den en az 3 okuma gelmiş mi kontrol et
    8. Değerler beklenen aralıklarda mı kontrol et
    """
    s = Settings()
    db = TimescaleDBDatabase(s)

    # DB erişilebilirlik kontrolü
    if not await _db_is_available(db):
        pytest.skip("TimescaleDB çalışmıyor — 'docker compose up -d' çalıştır")

    await db.connect()
    test_tag_ids = list(TAG_RANGES.keys())

    try:
        # Tablo temizliği — test izolasyonu (yalnız WS_* tag_id'lar).
        # TRUNCATE yapma: makinede custos.critical paralel çalışıyorsa
        # üretim okumalarını silmemek için hedefli DELETE kullanıyoruz.
        pool = db._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM tag_readings WHERE tag_id = ANY($1::text[])",
                test_tag_ids,
            )

        # Simülatörü başlat
        simulator = ModbusSimulator(host="127.0.0.1", port=5030)
        sim_task = asyncio.create_task(simulator.start())

        # Warm-up: simülatörün hazır olmasını bekle
        await asyncio.sleep(2)

        # Collector'ı başlat (DB'deki tag'ler yerine test tag'lerini kullan)
        collector = ModbusCollector(tags=TEST_TAGS, database=db)
        collector_task = asyncio.create_task(collector.start())

        # 5 saniye çalıştır
        await asyncio.sleep(5)

        # Collector'ı durdur
        await collector.stop()
        await collector_task

        # Simülatörü durdur
        simulator.stop()
        sim_task.cancel()
        try:
            await sim_task
        except asyncio.CancelledError:
            pass

        # Doğrulama: son 10 saniyedeki okumalar
        now = datetime.now(UTC)
        start = now - timedelta(seconds=10)

        for tag_id, (min_val, max_val) in TAG_RANGES.items():
            readings = await db.query_tag_readings(tag_id, start, now)

            # Her tag'den en az 3, en fazla 8 okuma bekliyoruz
            assert len(readings) >= 3, f"{tag_id}: en az 3 okuma beklendi, {len(readings)} geldi"
            assert len(readings) <= 8, f"{tag_id}: en fazla 8 okuma beklendi, {len(readings)} geldi"

            # Değerler beklenen aralıkta mı?
            for reading in readings:
                if reading.quality_flag == 0:
                    assert min_val <= reading.value <= max_val, (
                        f"{tag_id}: değer {reading.value} aralık dışı [{min_val}, {max_val}]"
                    )

    finally:
        # Temizlik — yalnız WS_* satırları sil, üretim tag'lerine dokunma
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM tag_readings WHERE tag_id = ANY($1::text[])",
                test_tag_ids,
            )
        await db.close()
