"""Veritabanı abstract arayüzü ve TimescaleDB implementasyonu.

Mimari prensip: tüm veritabanı erişimi bu modüldeki abstract
arayüz üzerinden yapılır. Modüllerden doğrudan SQL/ORM çağrısı
yapılmaz.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from datetime import datetime

import asyncpg
import structlog

from custos.shared.config import Settings

logger = structlog.get_logger(logger_name="database")


@dataclass(frozen=True)
class RawReading:
    """Tek bir ham sensör okuması.

    Collector'dan veritabanına aktarılan temel veri birimi.
    """

    timestamp: datetime
    sensor_id: str
    value: float
    quality_flag: int = 0


class DatabaseInterface(abc.ABC):
    """Veritabanı erişim arayüzü.

    Tüm modüller bu arayüz üzerinden veritabanına erişir.
    Concrete implementasyonlar (TimescaleDB, InMemory vb.)
    bu sınıfı miras alır.
    """

    @abc.abstractmethod
    async def connect(self) -> None:
        """Veritabanına bağlantı havuzu oluşturur."""

    @abc.abstractmethod
    async def close(self) -> None:
        """Bağlantı havuzunu kapatır."""

    @abc.abstractmethod
    async def health_check(self) -> bool:
        """Veritabanının erişilebilir olup olmadığını kontrol eder."""

    @abc.abstractmethod
    async def insert_raw_reading(
        self,
        timestamp: datetime,
        sensor_id: str,
        value: float,
        quality_flag: int,
    ) -> None:
        """Ham sensör okumasını kaydeder."""

    @abc.abstractmethod
    async def insert_raw_readings_batch(
        self,
        readings: list[RawReading],
    ) -> None:
        """Çoklu ham sensör okumasını tek batch halinde veritabanına yazar."""

    @abc.abstractmethod
    async def query_raw_readings(
        self,
        sensor_id: str,
        start: datetime,
        end: datetime,
    ) -> list[RawReading]:
        """Belirli bir sensörün zaman aralığındaki okumalarını sorgular."""

    @abc.abstractmethod
    async def insert_feature(
        self,
        timestamp: datetime,
        sensor_id: str,
        feature_name: str,
        feature_value: float,
        window_size_seconds: int,
    ) -> None:
        """Hesaplanmış bir özelliği kaydeder."""

    @abc.abstractmethod
    async def insert_label(
        self,
        timestamp_start: datetime,
        timestamp_end: datetime,
        event_type: str,
        confidence: str,
        source: str,
        notes: str | None,
    ) -> None:
        """Etiket kaydı oluşturur."""


class TimescaleDBDatabase(DatabaseInterface):
    """TimescaleDB (PostgreSQL) implementasyonu.

    asyncpg bağlantı havuzu kullanarak asenkron veritabanı
    erişimi sağlar.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._pool: asyncpg.Pool[asyncpg.Record] | None = None

    def _get_pool(self) -> asyncpg.Pool[asyncpg.Record]:
        """Bağlantı havuzunu döndürür, yoksa hata fırlatır."""
        if self._pool is None:
            msg = "Veritabanı bağlantı havuzu oluşturulmamış. connect() çağrıldı mı?"
            raise RuntimeError(msg)
        return self._pool

    async def connect(self) -> None:
        """asyncpg bağlantı havuzu oluşturur."""
        self._pool = await asyncpg.create_pool(
            dsn=self._settings.database_url_async,
            min_size=2,
            max_size=10,
            server_settings={"client_encoding": "UTF8"},
        )
        await logger.ainfo("Veritabanı bağlantı havuzu oluşturuldu")

    async def close(self) -> None:
        """Bağlantı havuzunu kapatır."""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
            await logger.ainfo("Veritabanı bağlantı havuzu kapatıldı")

    async def health_check(self) -> bool:
        """SELECT 1 ile veritabanı erişilebilirliğini kontrol eder."""
        if self._pool is None:
            await logger.awarning("Sağlık kontrolü: bağlantı havuzu yok")
            return False
        try:
            async with self._pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            return True
        except Exception:
            await logger.aerror("Sağlık kontrolü başarısız", exc_info=True)
            return False

    async def insert_raw_reading(
        self,
        timestamp: datetime,
        sensor_id: str,
        value: float,
        quality_flag: int,
    ) -> None:
        """Ham sensör okumasını kaydeder (batch'e delege eder)."""
        reading = RawReading(
            timestamp=timestamp,
            sensor_id=sensor_id,
            value=value,
            quality_flag=quality_flag,
        )
        await self.insert_raw_readings_batch([reading])

    async def insert_raw_readings_batch(
        self,
        readings: list[RawReading],
    ) -> None:
        """Çoklu ham sensör okumasını tek batch halinde veritabanına yazar."""
        pool = self._get_pool()
        args = [(r.timestamp, r.sensor_id, r.value, r.quality_flag) for r in readings]
        async with pool.acquire() as conn:
            await conn.executemany(
                "INSERT INTO raw_readings (timestamp, sensor_id, value, quality_flag) "
                "VALUES ($1, $2, $3, $4)",
                args,
            )

    async def query_raw_readings(
        self,
        sensor_id: str,
        start: datetime,
        end: datetime,
    ) -> list[RawReading]:
        """Belirli bir sensörün zaman aralığındaki okumalarını sorgular."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT timestamp, sensor_id, value, quality_flag "
                "FROM raw_readings "
                "WHERE sensor_id = $1 AND timestamp >= $2 AND timestamp <= $3 "
                "ORDER BY timestamp ASC",
                sensor_id,
                start,
                end,
            )
        return [
            RawReading(
                timestamp=row["timestamp"],
                sensor_id=row["sensor_id"],
                value=float(row["value"]),
                quality_flag=int(row["quality_flag"]),
            )
            for row in rows
        ]

    async def insert_feature(
        self,
        timestamp: datetime,
        sensor_id: str,
        feature_name: str,
        feature_value: float,
        window_size_seconds: int,
    ) -> None:
        """Hesaplanmış bir özelliği kaydeder."""
        raise NotImplementedError("Aşama 5'te eklenecek")

    async def insert_label(
        self,
        timestamp_start: datetime,
        timestamp_end: datetime,
        event_type: str,
        confidence: str,
        source: str,
        notes: str | None,
    ) -> None:
        """Etiket kaydı oluşturur."""
        raise NotImplementedError("Aşama 5'te eklenecek")


def create_database(settings: Settings) -> DatabaseInterface:
    """Veritabanı instance'ı oluşturan factory fonksiyonu.

    Şu an her zaman TimescaleDBDatabase döndürür. Abstract tip
    döndürdüğü için ileride başka implementasyonlara geçiş kolaydır.
    """
    return TimescaleDBDatabase(settings)
