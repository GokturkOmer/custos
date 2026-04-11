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

# Tag aralıkları (gain/offset uygulandıktan sonraki gerçek değerler)
TAG_RANGES: dict[str, tuple[float, float]] = {
    "T001": (20.0, 90.0),
    "P001": (0.0, 10.0),
    "F001": (0.0, 500.0),
    "V001": (0.0, 25.0),
    "R001": (0.0, 3000.0),
}

# Test tag'leri — sensors.toml'daki konfigürasyona karşılık gelir
TEST_TAGS: list[TagRecord] = [
    TagRecord(
        tag_id="T001", name="Temperature", modbus_host="127.0.0.1",
        modbus_port=5020, unit_id=1, register_address=40001,
        register_type="uint16", gain=1.0, offset=0.0, unit="°C",
    ),
    TagRecord(
        tag_id="P001", name="Pressure", modbus_host="127.0.0.1",
        modbus_port=5020, unit_id=1, register_address=40002,
        register_type="uint16", gain=0.1, offset=0.0, unit="bar",
    ),
    TagRecord(
        tag_id="F001", name="Flow", modbus_host="127.0.0.1",
        modbus_port=5020, unit_id=1, register_address=40003,
        register_type="uint16", gain=1.0, offset=0.0, unit="m³/h",
    ),
    TagRecord(
        tag_id="V001", name="Vibration", modbus_host="127.0.0.1",
        modbus_port=5020, unit_id=1, register_address=40004,
        register_type="uint16", gain=0.1, offset=0.0, unit="mm/s",
    ),
    TagRecord(
        tag_id="R001", name="RPM", modbus_host="127.0.0.1",
        modbus_port=5020, unit_id=1, register_address=40005,
        register_type="uint16", gain=1.0, offset=0.0, unit="RPM",
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
    2. tag_readings tablosunu temizle
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

    try:
        # Tablo temizliği — test izolasyonu
        pool = db._get_pool()
        async with pool.acquire() as conn:
            await conn.execute("TRUNCATE tag_readings")

        # Simülatörü başlat
        simulator = ModbusSimulator(host="127.0.0.1", port=5020)
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
            assert len(readings) <= 8, (
                f"{tag_id}: en fazla 8 okuma beklendi, {len(readings)} geldi"
            )

            # Değerler beklenen aralıkta mı?
            for reading in readings:
                if reading.quality_flag == 0:
                    assert min_val <= reading.value <= max_val, (
                        f"{tag_id}: değer {reading.value} aralık dışı [{min_val}, {max_val}]"
                    )

    finally:
        # Temizlik
        async with pool.acquire() as conn:
            await conn.execute("TRUNCATE tag_readings")
        await db.close()
