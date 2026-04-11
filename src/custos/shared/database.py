"""Veritabanı abstract arayüzü ve TimescaleDB implementasyonu.

Mimari prensip: tüm veritabanı erişimi bu modüldeki abstract
arayüz üzerinden yapılır. Modüllerden doğrudan SQL/ORM çağrısı
yapılmaz.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import UTC, datetime

import asyncpg
import structlog

from custos.shared.config import Settings

logger = structlog.get_logger(logger_name="database")


@dataclass(frozen=True)
class TagReading:
    """Tek bir tag okuması.

    Collector'dan veritabanına aktarılan temel veri birimi.
    """

    timestamp: datetime
    tag_id: str
    value: float
    quality_flag: int = 0


@dataclass
class TagRecord:
    """Tag tanım kaydı — tags tablosunun Python temsili."""

    tag_id: str
    name: str
    modbus_host: str
    register_address: int
    modbus_port: int = 502
    unit_id: int = 1
    register_type: str = "uint16"
    byte_order: str = "big"
    gain: float = 1.0
    offset: float = 0.0
    unit: str = ""
    polling_interval_ms: int = 10000
    polling_preset: str = "slow"
    status: str = "active"
    id: int | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


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

    # --- Tag Reading CRUD ---

    @abc.abstractmethod
    async def insert_tag_reading(
        self,
        timestamp: datetime,
        tag_id: str,
        value: float,
        quality_flag: int,
    ) -> None:
        """Tag okumasını kaydeder."""

    @abc.abstractmethod
    async def insert_tag_readings_batch(
        self,
        readings: list[TagReading],
    ) -> None:
        """Çoklu tag okumasını tek batch halinde veritabanına yazar."""

    @abc.abstractmethod
    async def query_tag_readings(
        self,
        tag_id: str,
        start: datetime,
        end: datetime,
    ) -> list[TagReading]:
        """Belirli bir tag'in zaman aralığındaki okumalarını sorgular."""

    # --- Tag CRUD ---

    @abc.abstractmethod
    async def insert_tag(self, tag: TagRecord) -> TagRecord:
        """Yeni tag kaydı oluşturur."""

    @abc.abstractmethod
    async def update_tag(self, tag_id: str, updates: dict[str, object]) -> TagRecord | None:
        """Tag kaydını günceller. Bulunamazsa None döndürür."""

    @abc.abstractmethod
    async def delete_tag(self, tag_id: str) -> bool:
        """Tag kaydını siler. Başarılıysa True döndürür."""

    @abc.abstractmethod
    async def get_tag(self, tag_id: str) -> TagRecord | None:
        """Tek bir tag kaydını getirir. Bulunamazsa None döndürür."""

    @abc.abstractmethod
    async def list_tags(self, status: str | None = None) -> list[TagRecord]:
        """Tag listesini döndürür. Opsiyonel status filtresi."""

    # --- Feature & Label (stub) ---

    @abc.abstractmethod
    async def insert_feature(
        self,
        timestamp: datetime,
        tag_id: str,
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


# İzin verilen güncelleme alanları (SQL injection önlemi)
_ALLOWED_TAG_UPDATE_FIELDS: frozenset[str] = frozenset({
    "name", "modbus_host", "modbus_port", "unit_id",
    "register_address", "register_type", "byte_order",
    "gain", "offset", "unit", "polling_interval_ms",
    "polling_preset", "status",
})


def _row_to_tag_record(row: asyncpg.Record) -> TagRecord:
    """asyncpg satırını TagRecord'a dönüştürür."""
    return TagRecord(
        id=row["id"],
        tag_id=row["tag_id"],
        name=row["name"],
        modbus_host=row["modbus_host"],
        modbus_port=row["modbus_port"],
        unit_id=row["unit_id"],
        register_address=row["register_address"],
        register_type=row["register_type"],
        byte_order=row["byte_order"],
        gain=float(row["gain"]),
        offset=float(row["offset"]),
        unit=row["unit"],
        polling_interval_ms=row["polling_interval_ms"],
        polling_preset=row["polling_preset"],
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


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

    # --- Tag Reading implementasyonları ---

    async def insert_tag_reading(
        self,
        timestamp: datetime,
        tag_id: str,
        value: float,
        quality_flag: int,
    ) -> None:
        """Tag okumasını kaydeder (batch'e delege eder)."""
        reading = TagReading(
            timestamp=timestamp,
            tag_id=tag_id,
            value=value,
            quality_flag=quality_flag,
        )
        await self.insert_tag_readings_batch([reading])

    async def insert_tag_readings_batch(
        self,
        readings: list[TagReading],
    ) -> None:
        """Çoklu tag okumasını tek batch halinde veritabanına yazar."""
        pool = self._get_pool()
        args = [(r.timestamp, r.tag_id, r.value, r.quality_flag) for r in readings]
        async with pool.acquire() as conn:
            await conn.executemany(
                "INSERT INTO tag_readings (timestamp, tag_id, value, quality_flag) "
                "VALUES ($1, $2, $3, $4)",
                args,
            )

    async def query_tag_readings(
        self,
        tag_id: str,
        start: datetime,
        end: datetime,
    ) -> list[TagReading]:
        """Belirli bir tag'in zaman aralığındaki okumalarını sorgular."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT timestamp, tag_id, value, quality_flag "
                "FROM tag_readings "
                "WHERE tag_id = $1 AND timestamp >= $2 AND timestamp <= $3 "
                "ORDER BY timestamp ASC",
                tag_id,
                start,
                end,
            )
        return [
            TagReading(
                timestamp=row["timestamp"],
                tag_id=row["tag_id"],
                value=float(row["value"]),
                quality_flag=int(row["quality_flag"]),
            )
            for row in rows
        ]

    # --- Tag CRUD implementasyonları ---

    async def insert_tag(self, tag: TagRecord) -> TagRecord:
        """Yeni tag kaydı oluşturur ve döndürür."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                'INSERT INTO tags '
                '(tag_id, name, modbus_host, modbus_port, unit_id, '
                'register_address, register_type, byte_order, '
                'gain, "offset", unit, polling_interval_ms, polling_preset, status) '
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14) "
                "RETURNING *",
                tag.tag_id, tag.name, tag.modbus_host, tag.modbus_port,
                tag.unit_id, tag.register_address, tag.register_type,
                tag.byte_order, tag.gain, tag.offset, tag.unit,
                tag.polling_interval_ms, tag.polling_preset, tag.status,
            )
        assert row is not None  # INSERT RETURNING her zaman satır döndürür
        return _row_to_tag_record(row)

    async def update_tag(self, tag_id: str, updates: dict[str, object]) -> TagRecord | None:
        """Tag kaydını günceller. Bilinmeyen alan varsa hata fırlatır."""
        invalid = set(updates.keys()) - _ALLOWED_TAG_UPDATE_FIELDS
        if invalid:
            msg = f"Güncellenemeyen alanlar: {invalid}"
            raise ValueError(msg)

        if not updates:
            return await self.get_tag(tag_id)

        # Dinamik SET cümlesi oluştur (alan adları whitelist'ten geldiği için güvenli)
        set_parts: list[str] = []
        values: list[object] = []
        for i, (col, val) in enumerate(updates.items(), start=1):
            # "offset" PostgreSQL reserved word olduğu için tırnak içine al
            col_name = f'"{col}"' if col == "offset" else col
            set_parts.append(f"{col_name} = ${i}")
            values.append(val)

        # updated_at'i de güncelle
        idx = len(values) + 1
        set_parts.append(f"updated_at = ${idx}")
        values.append(datetime.now(UTC))

        # WHERE koşulu
        idx_where = len(values) + 1
        values.append(tag_id)

        sql = f"UPDATE tags SET {', '.join(set_parts)} WHERE tag_id = ${idx_where} RETURNING *"

        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(sql, *values)

        if row is None:
            return None
        return _row_to_tag_record(row)

    async def delete_tag(self, tag_id: str) -> bool:
        """Tag kaydını siler."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM tags WHERE tag_id = $1",
                tag_id,
            )
        return str(result) == "DELETE 1"

    async def get_tag(self, tag_id: str) -> TagRecord | None:
        """Tek bir tag kaydını getirir."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM tags WHERE tag_id = $1",
                tag_id,
            )
        if row is None:
            return None
        return _row_to_tag_record(row)

    async def list_tags(self, status: str | None = None) -> list[TagRecord]:
        """Tag listesini döndürür."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            if status is not None:
                rows = await conn.fetch(
                    "SELECT * FROM tags WHERE status = $1 ORDER BY tag_id",
                    status,
                )
            else:
                rows = await conn.fetch("SELECT * FROM tags ORDER BY tag_id")
        return [_row_to_tag_record(row) for row in rows]

    # --- Feature & Label (stub) ---

    async def insert_feature(
        self,
        timestamp: datetime,
        tag_id: str,
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
