"""Modbus Collector modülü.

Tag'lerden Modbus TCP üzerinden veri okur ve veritabanına
batch halinde yazar. Critical loop'un ana bileşeni.

Mimari kural: Bu modül SADECE pymodbus ve abstract DB arayüzünü
kullanır. asyncpg, SQL string'leri veya ORM kodu burada YAZILMAZ.
Modbus write fonksiyonları ASLA çağrılmaz.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import structlog
from pymodbus.client import AsyncModbusTcpClient

from custos.shared.database import DatabaseInterface, TagReading, TagRecord

logger = structlog.get_logger(logger_name="collector")


class ModbusCollector:
    """Modbus üzerinden tag verisi okuyan ve DB'ye yazan collector.

    Her okuma döngüsünde tüm tag'leri okur, TagReading listesi oluşturur
    ve tek batch halinde veritabanına yazar.
    """

    def __init__(
        self,
        tags: list[TagRecord],
        database: DatabaseInterface,
    ) -> None:
        self._tags = tags
        self._database = database
        self._clients: dict[tuple[str, int], AsyncModbusTcpClient] = {}
        self._shutdown_event = asyncio.Event()

    async def _get_or_create_client(self, host: str, port: int) -> AsyncModbusTcpClient:
        """Verilen host:port için Modbus client döndürür, yoksa oluşturur."""
        key = (host, port)
        if key not in self._clients:
            client = AsyncModbusTcpClient(host, port=port)
            self._clients[key] = client
        return self._clients[key]

    async def _ensure_connected(self, client: AsyncModbusTcpClient, host: str, port: int) -> bool:
        """Client'ın bağlı olduğundan emin olur, değilse bağlanmayı dener."""
        if client.connected:
            return True

        await logger.ainfo(
            "Modbus bağlantısı kuruluyor",
            host=host,
            port=port,
        )
        connected: bool = await client.connect()
        if connected:
            await logger.ainfo(
                "Modbus bağlantısı kuruldu",
                host=host,
                port=port,
            )
        else:
            await logger.aerror(
                "Modbus bağlantısı kurulamadı",
                host=host,
                port=port,
            )
        return connected

    async def _read_tag(self, tag: TagRecord) -> TagReading:
        """Tek bir tag'i okur ve TagReading döndürür.

        Okuma hatası durumunda quality_flag=1 ile değer 0.0 döndürür.
        """
        now = datetime.now(UTC)
        client = await self._get_or_create_client(tag.modbus_host, tag.modbus_port)

        if not await self._ensure_connected(client, tag.modbus_host, tag.modbus_port):
            await logger.awarning(
                "Tag okunamadı: bağlantı yok",
                tag_id=tag.tag_id,
            )
            return TagReading(
                timestamp=now,
                tag_id=tag.tag_id,
                value=0.0,
                quality_flag=1,
            )

        try:
            response: Any = await client.read_holding_registers(
                tag.register_address,
                count=1,
                device_id=tag.unit_id,
            )
            if response.isError():
                await logger.awarning(
                    "Tag okuma hatası",
                    tag_id=tag.tag_id,
                    hata=str(response),
                )
                return TagReading(
                    timestamp=now,
                    tag_id=tag.tag_id,
                    value=0.0,
                    quality_flag=1,
                )

            raw_value: int = response.registers[0]
            scaled_value = raw_value * tag.gain + tag.offset

            return TagReading(
                timestamp=now,
                tag_id=tag.tag_id,
                value=scaled_value,
                quality_flag=0,
            )

        except Exception:
            await logger.aerror(
                "Tag okuma exception",
                tag_id=tag.tag_id,
                exc_info=True,
            )
            return TagReading(
                timestamp=now,
                tag_id=tag.tag_id,
                value=0.0,
                quality_flag=1,
            )

    async def _run_cycle(self) -> None:
        """Tek bir okuma döngüsü: tüm tag'leri oku, batch yaz."""
        readings: list[TagReading] = []
        for tag in self._tags:
            reading = await self._read_tag(tag)
            readings.append(reading)

        # Batch yazma
        try:
            await self._database.insert_tag_readings_batch(readings)
            ok_count = sum(1 for r in readings if r.quality_flag == 0)
            fail_count = len(readings) - ok_count
            await logger.ainfo(
                "Batch yazıldı",
                toplam=len(readings),
                başarılı=ok_count,
                hatalı=fail_count,
            )
        except Exception:
            await logger.aerror(
                "Batch yazma başarısız",
                exc_info=True,
            )

    async def start(self) -> None:
        """Collector'ı başlatır. Sonsuz döngüde çalışır, shutdown ile durur."""
        await logger.ainfo(
            "Collector başlatılıyor",
            tag_sayısı=len(self._tags),
        )

        while not self._shutdown_event.is_set():
            cycle_start = asyncio.get_event_loop().time()

            await self._run_cycle()

            elapsed = asyncio.get_event_loop().time() - cycle_start
            sleep_time = max(0.0, 1.0 - elapsed)

            if elapsed > 1.0:
                await logger.awarning(
                    "Döngü yavaşladı",
                    süre_sn=round(elapsed, 3),
                )

            # Shutdown event veya sleep — hangisi önce gelirse
            try:
                await asyncio.wait_for(
                    self._shutdown_event.wait(),
                    timeout=sleep_time,
                )
                break
            except TimeoutError:
                pass

        await logger.ainfo("Collector döngüsü sona erdi")

    async def stop(self) -> None:
        """Temiz kapanış: Modbus bağlantılarını kapat."""
        await logger.ainfo("Collector durduruluyor")
        self._shutdown_event.set()

        # Modbus client'ları kapat
        for (host, port), client in self._clients.items():
            client.close()
            await logger.ainfo(
                "Modbus bağlantısı kapatıldı",
                host=host,
                port=port,
            )
        self._clients.clear()

        await logger.ainfo("Collector durduruldu")
