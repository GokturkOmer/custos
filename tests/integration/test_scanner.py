"""Modbus Auto-Scan entegrasyon testleri.

Hem TimescaleDB hem de Modbus simülatörünün ayakta olmasını gerektirir.
Simülatör: python -m custos.simulator (port 5020)
"""

from __future__ import annotations

import asyncio

import pytest

from custos.shared.config import Settings
from custos.shared.database import ConnectionProfile, TimescaleDBDatabase

# Simülatörü import et (test içinde başlatacağız)
from custos.simulator.modbus_server import ModbusSimulator

# Her test farklı port kullanır (TIME_WAIT port çakışmasını önler)
_next_port = 5020


@pytest.fixture
def _check_db_available() -> None:
    """TimescaleDB erişilebilir değilse testi atla."""

    async def _probe() -> bool:
        s = Settings()
        db = TimescaleDBDatabase(s)
        try:
            await db.connect()
            result = await db.health_check()
            await db.close()
        except Exception:
            return False
        else:
            return result

    if not asyncio.run(_probe()):
        pytest.skip("TimescaleDB ayakta değil — 'docker compose up -d' çalıştır")


@pytest.fixture
async def db() -> TimescaleDBDatabase:
    """Test için DB bağlantısı oluşturur ve temizler."""
    s = Settings()
    database = TimescaleDBDatabase(s)
    await database.connect()
    pool = database._get_pool()
    # Test öncesi temizlik
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM connection_profiles WHERE name LIKE 'TEST_%'"
        )
        await conn.execute(
            "DELETE FROM tags WHERE tag_id LIKE 'tag_127.0.0.1_%'"
        )
    yield database  # type: ignore[misc]
    # Test sonrası temizlik
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM connection_profiles WHERE name LIKE 'TEST_%'"
        )
        await conn.execute(
            "DELETE FROM tags WHERE tag_id LIKE 'tag_127.0.0.1_%'"
        )
    await database.close()


@pytest.fixture
async def simulator() -> ModbusSimulator:
    """Test için Modbus simülatör başlatır ve durdurur.

    Her test farklı port kullanır — pymodbus TCP server kapandıktan
    sonra OS soketi TIME_WAIT'te tutabilir, aynı portu hemen bind
    etmek başarısız olur.
    """
    global _next_port  # noqa: PLW0603
    port = _next_port
    _next_port += 1

    sim = ModbusSimulator(host="127.0.0.1", port=port)
    task = asyncio.create_task(sim.start())
    # Simülatörün hazır olmasını bekle
    await asyncio.sleep(0.5)
    yield sim  # type: ignore[misc]
    sim.stop()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.usefixtures("_check_db_available")
async def test_scanner_discovers_registers(
    db: TimescaleDBDatabase,
    simulator: ModbusSimulator,
) -> None:
    """Scanner simülatördeki register'ları keşfedebiliyor mu?"""
    # Scanner'ı import et (pymodbus gerekli)
    from custos.analytics.scanner import ModbusScanner

    # Connection profile oluştur (simülatörün portunu kullan)
    profile = ConnectionProfile(
        name="TEST_SCAN",
        host="127.0.0.1",
        port=simulator._port,
        unit_id_start=1,
        unit_id_end=1,
    )
    created = await db.insert_connection_profile(profile)
    assert created.id is not None

    # Güncel profili çek (id ile)
    profile_with_id = await db.get_connection_profile(created.id)
    assert profile_with_id is not None

    # Scan başlat
    scanner = ModbusScanner(profile=profile_with_id, database=db)
    results = await scanner.scan()

    # Simülatörde 5 register var (0-4)
    assert len(results) >= 1, "En az bir register keşfedilmeli"

    # Profil durumu güncellenmeli
    updated_profile = await db.get_connection_profile(created.id)
    assert updated_profile is not None
    assert updated_profile.status == "completed"
    assert updated_profile.last_scan_at is not None

    # Latency ölçülmüş olmalı
    assert updated_profile.slave_latency_avg_ms is not None
    assert updated_profile.slave_latency_avg_ms > 0

    # Discovered tag'ler oluşturulmuş olmalı
    discovered = await db.list_tags(status="discovered")
    scan_tags = [
        t for t in discovered
        if t.modbus_host == "127.0.0.1" and t.modbus_port == simulator._port
    ]
    assert len(scan_tags) >= 1, "En az bir discovered tag oluşturulmalı"


@pytest.mark.usefixtures("_check_db_available")
async def test_scanner_no_duplicate_tags(
    db: TimescaleDBDatabase,
    simulator: ModbusSimulator,
) -> None:
    """Aynı scan iki kez çalıştırılınca duplicate tag oluşmamalı."""
    from custos.analytics.scanner import ModbusScanner

    profile = ConnectionProfile(
        name="TEST_DUP",
        host="127.0.0.1",
        port=simulator._port,
        unit_id_start=1,
        unit_id_end=1,
    )
    created = await db.insert_connection_profile(profile)
    assert created.id is not None

    profile_with_id = await db.get_connection_profile(created.id)
    assert profile_with_id is not None

    # İlk scan
    scanner1 = ModbusScanner(profile=profile_with_id, database=db)
    await scanner1.scan()

    # İkinci scan
    # Profil durumunu sıfırla
    await db.update_connection_profile(created.id, {"status": "idle"})
    profile_refreshed = await db.get_connection_profile(created.id)
    assert profile_refreshed is not None

    scanner2 = ModbusScanner(profile=profile_refreshed, database=db)
    await scanner2.scan()

    # Tag sayısı artmamalı (aynı register'lar zaten keşfedilmiş)
    discovered = await db.list_tags(status="discovered")
    scan_tags = [
        t for t in discovered
        if t.modbus_host == "127.0.0.1" and t.modbus_port == simulator._port
    ]
    # Her register için en fazla 1 tag olmalı
    addresses = [t.register_address for t in scan_tags]
    assert len(addresses) == len(set(addresses)), "Duplicate tag oluşturulmamalı"
