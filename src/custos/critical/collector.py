"""Modbus Collector modülü.

Tag'lerden Modbus TCP üzerinden veri okur ve veritabanına
batch halinde yazar. Critical loop'un ana bileşeni.

Per-tag polling desteği: her tag kendi polling_interval_ms
değerine göre okunur. GCD base tick yaklaşımı kullanılır.

Batch Modbus read (F11 Paket I): Komşu register'lar `batch_grouper`
aracılığıyla tek `read_holding_registers(start, count=N)` çağrısında
okunur — PLC başına ~10x daha az TCP round-trip. Batch hatasında
per-tag fallback (eski yol) devreye girer; tek bozuk tag tüm batch'i
düşürmez. `collector_batch_read_enabled=False` ile kapatılabilir.

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

from custos.critical.batch_grouper import BatchGroup, group_tags_for_batch_read
from custos.critical.register_decoder import (
    RegisterDecodeError,
    decode_register,
    expected_word_count,
)
from custos.shared.database import DatabaseInterface, TagReading, TagRecord

logger = structlog.get_logger(logger_name="collector")

# Varsayılan değerler — Settings override etmezse kullanılır.
_DEFAULT_FAST_POLLING_BUDGET = 10
_DEFAULT_PER_HOST_CONCURRENCY = 5
_DEFAULT_BATCH_READ_ENABLED = True
_DEFAULT_BATCH_GAP_TOLERANCE = 8

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
        batch_read_enabled: bool = _DEFAULT_BATCH_READ_ENABLED,
        batch_gap_tolerance: int = _DEFAULT_BATCH_GAP_TOLERANCE,
    ) -> None:
        self._tags = [t for t in tags if t.status == "active"]
        self._database = database
        self._clients: dict[tuple[str, int], AsyncModbusTcpClient] = {}
        self._shutdown_event = asyncio.Event()
        self._per_host_concurrency = max(1, per_host_concurrency)
        self._fast_polling_budget = fast_polling_budget

        # Batch read ayarları (F11 Paket I)
        self._batch_read_enabled = batch_read_enabled
        self._batch_gap_tolerance = max(0, batch_gap_tolerance)

        # Per-tag polling durumu
        self._next_due: dict[str, float] = {}
        self._base_tick_ms = _compute_base_tick_ms(self._tags)
        self._tick_count = 0

        # Tick metrikleri — yük testi ve gözlemlenebilirlik için
        self._total_tick_count = 0
        self._slow_tick_count = 0

        # Batch metrikleri (Paket E observability)
        self._batch_read_count = 0
        self._single_read_count = 0
        self._batch_fallback_count = 0

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

        Batch path (default): `group_tags_for_batch_read` ile komşu
        register'lar tek çağrıda okunur. Batch hatasında per-tag fallback.
        Single path (batch_read_enabled=False): eski per-tag okuma — acil
        geri dönüş için feature flag.

        `Semaphore` ile aynı anda en fazla `per_host_concurrency` okuma
        yapılır. Async Modbus client tek TCP socket üstünden sıraya alır;
        bütçeyi sınırlamak slave'i (8-32 concurrent connection tipik) korur.
        """
        sem = asyncio.Semaphore(self._per_host_concurrency)

        if not self._batch_read_enabled:
            # Feature flag kapalı → eski per-tag yol
            async def _one(t: TagRecord) -> TagReading:
                async with sem:
                    self._single_read_count += 1
                    return await self._read_tag(t)

            return await asyncio.gather(*[_one(t) for t in tags])

        # Batch path — gruplama aynı host'ta unit_id bazında da ayırır.
        batches = group_tags_for_batch_read(
            tags, gap_tolerance=self._batch_gap_tolerance
        )

        async def _one_batch(batch: BatchGroup) -> list[TagReading]:
            async with sem:
                return await self._read_batch(batch)

        batch_results = await asyncio.gather(
            *[_one_batch(b) for b in batches]
        )
        return [r for batch_readings in batch_results for r in batch_readings]

    async def _read_batch(self, batch: BatchGroup) -> list[TagReading]:
        """Tek bir batch'i okur, decode eder, TagReading listesi döner.

        Hata yönetimi (atomicity):
            - Bağlantı yoksa: batch'in tüm tag'leri quality_flag=1.
            - response.isError() veya exception: per-tag fallback
              (`_fallback_read_tags`) — tek bozuk tag tüm batch'i düşürmez.
            - Tek tag'in decode hatası: o tag quality_flag=1, diğerleri
              başarılı.
        """
        now = datetime.now(UTC)
        client = await self._get_or_create_client(
            batch.modbus_host, batch.modbus_port
        )

        if not await self._ensure_connected(
            client, batch.modbus_host, batch.modbus_port
        ):
            await logger.awarning(
                "Batch okunamadı: bağlantı yok",
                host=batch.modbus_host,
                port=batch.modbus_port,
                start=batch.start_address,
                tag_sayısı=batch.tag_count,
            )
            return [
                TagReading(
                    timestamp=now,
                    tag_id=t.tag_id,
                    value=0.0,
                    quality_flag=1,
                )
                for t in batch.tags
            ]

        loop = asyncio.get_event_loop()
        try:
            t0 = loop.time()
            response: Any = await client.read_holding_registers(
                batch.start_address,
                count=batch.count,
                device_id=batch.unit_id,
            )
            duration_ms = (loop.time() - t0) * 1000.0

            if response.isError():
                await logger.awarning(
                    "Batch okuma hatası, per-tag fallback",
                    host=batch.modbus_host,
                    start=batch.start_address,
                    count=batch.count,
                    hata=str(response),
                )
                self._batch_fallback_count += 1
                return await self._fallback_read_tags(batch.tags)

            registers = list(response.registers)
            if len(registers) < batch.count:
                await logger.awarning(
                    "Batch response eksik register, fallback",
                    alınan=len(registers),
                    beklenen=batch.count,
                )
                self._batch_fallback_count += 1
                return await self._fallback_read_tags(batch.tags)

            readings = self._decode_batch_response(batch, registers, now)
            self._batch_read_count += 1

            await logger.adebug(
                "Batch okundu",
                host=batch.modbus_host,
                start=batch.start_address,
                count=batch.count,
                tag_sayısı=batch.tag_count,
                süre_ms=round(duration_ms, 1),
            )
            return readings

        except Exception:
            await logger.aerror(
                "Batch okuma exception, per-tag fallback",
                host=batch.modbus_host,
                start=batch.start_address,
                exc_info=True,
            )
            self._batch_fallback_count += 1
            return await self._fallback_read_tags(batch.tags)

    def _decode_batch_response(
        self,
        batch: BatchGroup,
        registers: list[int],
        timestamp: datetime,
    ) -> list[TagReading]:
        """Batch response'undan her tag için register'ı kes + decode.

        Tek tag'in decode hatası diğerlerini etkilemez (quality_flag=1).
        """
        readings: list[TagReading] = []
        for tag in batch.tags:
            offset = tag.register_address - batch.start_address
            span = expected_word_count(tag.register_type)
            try:
                raw = tuple(registers[offset : offset + span])
                if len(raw) != span:
                    raise RegisterDecodeError(
                        f"Tag {tag.tag_id}: offset {offset} için yeterli "
                        f"register yok (span={span}, toplam={len(registers)})"
                    )
                value = decode_register(raw, tag)
                readings.append(
                    TagReading(
                        timestamp=timestamp,
                        tag_id=tag.tag_id,
                        value=value,
                        quality_flag=0,
                    )
                )
            except RegisterDecodeError as e:
                # Tek tag'in decode hatası — log + quality_flag=1, batch'in
                # diğer tag'leri devam eder.
                logger.warning(
                    "Tag decode hatası",
                    tag_id=tag.tag_id,
                    hata=str(e),
                )
                readings.append(
                    TagReading(
                        timestamp=timestamp,
                        tag_id=tag.tag_id,
                        value=0.0,
                        quality_flag=1,
                    )
                )
        return readings

    async def _fallback_read_tags(
        self, tags: list[TagRecord]
    ) -> list[TagReading]:
        """Batch başarısız olduğunda tag bazlı per-tag retry.

        Eski single-read path'i kullanır; tek bozuk tag tüm batch'i düşürmez.
        Budget semaphore bu noktada zaten dış kapsamda tutulduğu için burada
        tekrar sınırlama yapılmaz.
        """
        self._single_read_count += len(tags)
        return await asyncio.gather(*[self._read_tag(t) for t in tags])

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
                batch_okuma=self._batch_read_count,
                tekil_okuma=self._single_read_count,
                batch_fallback=self._batch_fallback_count,
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
