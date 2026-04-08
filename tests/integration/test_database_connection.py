"""Veritabanı bağlantı entegrasyon testleri.

Bu testler TimescaleDB'nin ayakta olmasını gerektirir.
Veritabanı yoksa ilgili testler atlanır.
"""

from __future__ import annotations

import asyncio

import pytest

from custos.shared.config import Settings
from custos.shared.database import TimescaleDBDatabase, create_database


def test_settings_loads() -> None:
    """Settings sınıfı hatasız yükleniyor mu?"""
    s = Settings()
    assert s.postgres_db
    assert s.postgres_user
    assert s.database_url


def test_database_instance_created() -> None:
    """TimescaleDBDatabase instance'ı veritabanına bağlanmadan oluşturulabiliyor mu?"""
    s = Settings()
    db = create_database(s)
    assert isinstance(db, TimescaleDBDatabase)


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
        pytest.skip("TimescaleDB ayakta değil mi? 'docker compose up -d' çalıştırdın mı?")


@pytest.mark.usefixtures("_check_db_available")
async def test_health_check_returns_true() -> None:
    """Gerçek veritabanına bağlanıp health_check True döndürüyor mu?"""
    s = Settings()
    db = TimescaleDBDatabase(s)
    await db.connect()
    try:
        result = await db.health_check()
        assert result is True
    finally:
        await db.close()


async def test_health_check_returns_false_on_bad_host() -> None:
    """Yanlış host'a bağlanmaya çalışırken health_check False döndürüyor mu?"""
    s = Settings(
        postgres_host="192.0.2.1",  # RFC 5737 — erişilemez test adresi
        postgres_port=5432,
    )
    db = TimescaleDBDatabase(s)
    # connect() başarısız olabilir, health_check False döndürmeli
    try:
        await db.connect()
    except Exception:
        pass
    result = await db.health_check()
    assert result is False
