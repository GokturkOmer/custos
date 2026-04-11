"""Modbus Collector modülü.

Tag'lerden Modbus TCP üzerinden veri okur ve veritabanına
batch halinde yazar. Critical loop'un ana bileşeni.

Per-tag polling desteği: her tag kendi polling_interval_ms
değerine göre okunur. GCD base tick yaklaşımı kullanılır.

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

# Fast polling budget sınırı (<=1000ms interval'e sahip tag sayısı)
_FAST_POLLING_BUDGET = 10

# Tag listesi yenileme aralığı (tick sayısı)
_TAG_REFRESH_INTERVAL = 60

# Minimum base tick (ms) — çok küçük tick'ler CPU israfına yol açar
_MIN_BASE_TICK_MS = 50


def _gcd(a: int, b: int) -> int:
    """İki sayının en büyük ortak bölenini hesaplar."""
    while b:
        a, b = b, a % b
    return a


def _compute_base_tick_ms(tags: list[TagRecord]) -> int:
    """Tag'lerin polling interval'lerinin GCD'sini hesaplar.

    Minimum _MIN_BASE_TICK_MS döndürür.
    """
    if not tags:
        return 1000

    intervals = [t.polling_interval_ms for t in tags]
    result = intervals[0]
    for interval in intervals[1:]:
        result = _gcd(result, interval)

    return max(result, _MIN_BASE_TICK_MS)


class ModbusCollector:
    """Modbus üzerinden tag verisi okuyan ve DB'ye yazan collector.

    Per-tag polling: her tag kendi interval'inde okunur.
    Base tick yaklaşımı ile tek döngü tüm tag'leri yönetir.
    """

    def __init__(
        self,
        tags: list[TagRecord],
        database: DatabaseInterface,
    ) -> None:
        self._tags = [t for t in tags if t.status == "active"]
        self._database = database
        self._clients: dict[tuple[str, int], AsyncModbusTcpClient] = {}
        self._shutdown_event = asyncio.Event()

        # Per-tag polling durumu
        self._next_due: dict[str, float] = {}
        self._base_tick_ms = _compute_base_tick_ms(self._tags)
        self._tick_count = 0

        # Fast polling budget kontrolü
        self._check_fast_polling_budget()

    def _check_fast_polling_budget(self) -> None:
        """Fast polling budget kontrolü yapar, aşılırsa uyarır."""
        fast_count = sum(
            1 for t in self._tags if t.polling_interval_ms <= 1000
        )
        if fast_count > _FAST_POLLING_BUDGET:
            # structlog senkron kullanım (init'te async yok)
            structlog.get_logger(logger_name="collector").warning(
                "Fast polling budget aşıldı",
                fast_tag_sayısı=fast_count,
                budget=_FAST_POLLING_BUDGET,
            )

    def _init_schedule(self) -> None:
        """Tag'lerin ilk okuma zamanlarını ayarlar."""
        now = asyncio.get_event_loop().time()
        for tag in self._tags:
            if tag.tag_id not in self._next_due:
                self._next_due[tag.tag_id] = now

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

    async def _run_tick(self) -> None:
        """Tek bir tick: süresi gelen tag'leri oku, batch yaz."""
        now = asyncio.get_event_loop().time()

        # Süresi gelen tag'leri bul
        due_tags = [
            t for t in self._tags
            if self._next_due.get(t.tag_id, 0.0) <= now
        ]

        if not due_tags:
            return

        # Tag'leri oku
        readings: list[TagReading] = []
        for tag in due_tags:
            reading = await self._read_tag(tag)
            readings.append(reading)
            # Sonraki okuma zamanını ayarla
            self._next_due[tag.tag_id] = now + (tag.polling_interval_ms / 1000.0)

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

    async def _refresh_tags(self) -> None:
        """DB'den tag listesini yeniler. Yeni tag ekler, kaldırılanları çıkarır."""
        try:
            fresh_tags = await self._database.list_tags(status="active")
        except Exception:
            await logger.aerror(
                "Tag listesi yenilenemedi",
                exc_info=True,
            )
            return

        old_ids = {t.tag_id for t in self._tags}
        new_ids = {t.tag_id for t in fresh_tags}

        # Eklenen tag'ler
        added = new_ids - old_ids
        removed = old_ids - new_ids

        if added or removed:
            await logger.ainfo(
                "Tag listesi güncellendi",
                eklenen=len(added),
                çıkarılan=len(removed),
            )

        # Listesini güncelle
        self._tags = fresh_tags

        # Kaldırılan tag'lerin schedule'ını temizle
        for tag_id in removed:
            self._next_due.pop(tag_id, None)

        # Yeni tag'lerin schedule'ını oluştur
        now = asyncio.get_event_loop().time()
        for tag_id in added:
            self._next_due[tag_id] = now

        # Base tick'i yeniden hesapla
        self._base_tick_ms = _compute_base_tick_ms(self._tags)

        # Fast polling budget kontrolü
        fast_count = sum(
            1 for t in self._tags if t.polling_interval_ms <= 1000
        )
        if fast_count > _FAST_POLLING_BUDGET:
            await logger.awarning(
                "Fast polling budget aşıldı",
                fast_tag_sayısı=fast_count,
                budget=_FAST_POLLING_BUDGET,
            )

    async def start(self) -> None:
        """Collector'ı başlatır. Per-tag polling ile çalışır."""
        await logger.ainfo(
            "Collector başlatılıyor",
            tag_sayısı=len(self._tags),
            base_tick_ms=self._base_tick_ms,
        )

        self._init_schedule()

        while not self._shutdown_event.is_set():
            cycle_start = asyncio.get_event_loop().time()

            await self._run_tick()

            # Tag yenileme kontrolü
            self._tick_count += 1
            if self._tick_count >= _TAG_REFRESH_INTERVAL:
                self._tick_count = 0
                await self._refresh_tags()

            # Base tick kadar bekle
            elapsed = asyncio.get_event_loop().time() - cycle_start
            sleep_time = max(0.0, (self._base_tick_ms / 1000.0) - elapsed)

            if elapsed > (self._base_tick_ms / 1000.0):
                await logger.awarning(
                    "Tick yavaşladı",
                    süre_ms=round(elapsed * 1000, 1),
                    base_tick_ms=self._base_tick_ms,
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
