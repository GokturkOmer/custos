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
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

import structlog
from pymodbus.client import AsyncModbusTcpClient

from custos.shared.database import DatabaseInterface, TagReading, TagRecord

logger = structlog.get_logger(logger_name="collector")

# Varsayılan değerler — Settings override etmezse kullanılır.
_DEFAULT_FAST_POLLING_BUDGET = 10
_DEFAULT_PER_HOST_CONCURRENCY = 5

# Fast polling eşiği (ms). Bu değerin altındaki polling "fast" sayılır.
_FAST_POLLING_THRESHOLD_MS = 1000

# Tag listesi yenileme aralığı (tick sayısı)
_TAG_REFRESH_INTERVAL = 60

# Minimum base tick (ms) — çok küçük tick'ler CPU israfına yol açar
_MIN_BASE_TICK_MS = 50


class FastPollingBudgetError(ValueError):
    """Fast polling bütçesi aşıldığında fırlatılan istisna.

    Collector başlatılırken veya tag aktivasyonunda bütçe kontrolü
    başarısız olursa bu hata atılır.
    """


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
        per_host_concurrency: int = _DEFAULT_PER_HOST_CONCURRENCY,
        fast_polling_budget: int = _DEFAULT_FAST_POLLING_BUDGET,
    ) -> None:
        self._tags = [t for t in tags if t.status == "active"]
        self._database = database
        self._clients: dict[tuple[str, int], AsyncModbusTcpClient] = {}
        self._shutdown_event = asyncio.Event()
        self._per_host_concurrency = max(1, per_host_concurrency)
        self._fast_polling_budget = fast_polling_budget

        # Per-tag polling durumu
        self._next_due: dict[str, float] = {}
        self._base_tick_ms = _compute_base_tick_ms(self._tags)
        self._tick_count = 0

        # Tick metrikleri — yük testi ve gözlemlenebilirlik için
        self._total_tick_count = 0
        self._slow_tick_count = 0

        # Fast polling budget kontrolü — aşım varsa init'te raise edilir.
        self._enforce_fast_polling_budget()

    @property
    def slow_tick_ratio(self) -> float:
        """Toplam tick'lerin kaçı base_tick_ms'yi aştı? 0.0-1.0 arasında."""
        if self._total_tick_count == 0:
            return 0.0
        return self._slow_tick_count / self._total_tick_count

    @property
    def total_tick_count(self) -> int:
        """Toplam tick sayısı — yük testi metriği için."""
        return self._total_tick_count

    def _count_fast_tags(self, tags: list[TagRecord] | None = None) -> int:
        """Fast polling (polling_interval_ms <= eşik) tag sayısını döndürür."""
        source = tags if tags is not None else self._tags
        return sum(
            1 for t in source if t.polling_interval_ms <= _FAST_POLLING_THRESHOLD_MS
        )

    def _enforce_fast_polling_budget(self) -> None:
        """Init-time bütçe kontrolü — aşımda FastPollingBudgetError atar."""
        fast_count = self._count_fast_tags()
        if fast_count > self._fast_polling_budget:
            msg = (
                f"Fast polling bütçesi aşıldı: {fast_count} fast tag aktif, "
                f"bütçe {self._fast_polling_budget}. Collector başlatılamaz. "
                f"Mevcut bir fast tag'i Slow'a çekin veya bütçeyi artırın."
            )
            structlog.get_logger(logger_name="collector").error(
                "Fast polling budget aşıldı (init)",
                fast_tag_sayısı=fast_count,
                budget=self._fast_polling_budget,
            )
            raise FastPollingBudgetError(msg)

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

    async def _read_host_group(
        self,
        tags: list[TagRecord],
    ) -> list[TagReading]:
        """Tek bir (host, port) için tag'leri paralel okur.

        `Semaphore` ile aynı anda en fazla `per_host_concurrency` okuma
        yapılır. Async Modbus client tek TCP socket üstünden sıraya alır;
        bütçeyi sınırlamak slave'i (8-32 concurrent connection tipik) korur.
        """
        sem = asyncio.Semaphore(self._per_host_concurrency)

        async def _one(t: TagRecord) -> TagReading:
            async with sem:
                return await self._read_tag(t)

        return await asyncio.gather(*[_one(t) for t in tags])

    async def _run_tick(self) -> None:
        """Tek bir tick: süresi gelen tag'leri oku, batch yaz.

        Paralelleştirme: due_tags (host, port) ile gruplanır, her grup
        bounded concurrency ile okunur, hostlar arası da paralel çalışır.
        """
        now = asyncio.get_event_loop().time()

        # Süresi gelen tag'leri bul
        due_tags = [
            t for t in self._tags
            if self._next_due.get(t.tag_id, 0.0) <= now
        ]

        if not due_tags:
            return

        # Host bazlı gruplama — her grup ayrı TCP bağlantısı kullanır
        by_host: dict[tuple[str, int], list[TagRecord]] = defaultdict(list)
        for tag in due_tags:
            by_host[(tag.modbus_host, tag.modbus_port)].append(tag)

        # Sonraki okuma zamanını `now` snapshot'ına göre ayarla.
        # I/O öncesi set ediyoruz: okuma süresi tick boyutuna kıyasla
        # uzun sürerse bir sonraki tick tekrar tetiklenmez.
        for tag in due_tags:
            self._next_due[tag.tag_id] = now + (tag.polling_interval_ms / 1000.0)

        # Her host grubunu paralel başlat, hepsini bir arada bekle
        host_results = await asyncio.gather(
            *[self._read_host_group(tags) for tags in by_host.values()]
        )
        readings: list[TagReading] = [r for host in host_results for r in host]

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
                host_sayısı=len(by_host),
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

        # Fast polling budget kontrolü — runtime'da crash etmiyoruz, çünkü API
        # katmanı aktivasyonda reddi yapıyor. DB'ye direkt insert gibi bir
        # kaçak olduysa burada error log + devam.
        fast_count = self._count_fast_tags()
        if fast_count > self._fast_polling_budget:
            await logger.aerror(
                "Fast polling budget runtime'da aşıldı (API atlanmış olabilir)",
                fast_tag_sayısı=fast_count,
                budget=self._fast_polling_budget,
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

            self._total_tick_count += 1
            if elapsed > (self._base_tick_ms / 1000.0):
                self._slow_tick_count += 1
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
