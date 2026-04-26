"""Modbus Auto-Scan Engine.

Connection profile üzerinden Modbus slave'leri tarar, register'ları
keşfeder ve aday tag'ler oluşturur. Analytics loop'ta çalışır,
critical loop'tan bağımsızdır.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

import structlog
from pymodbus.client import AsyncModbusTcpClient

from custos.shared.database import ConnectionProfile, DatabaseInterface, TagRecord

logger = structlog.get_logger(logger_name="scanner")

# Scan parametreleri
_LATENCY_PROBE_COUNT = 10
_TYPE_INFERENCE_SAMPLE_COUNT = 5
_REGISTER_SCAN_START = 0
_REGISTER_SCAN_END = 99
_READ_TIMEOUT_SEC = 2.0
_SAMPLE_DELAY_SEC = 0.2


@dataclass
class ScanResult:
    """Tek bir register keşif sonucu."""

    slave_id: int
    register_address: int
    raw_values: list[int] = field(default_factory=list)
    inferred_type: str = "uint16"
    byte_order: str = "big"
    stability_note: str = ""


class ModbusScanner:
    """Modbus auto-scan motoru.

    Bir connection profile üzerinden slave tarama, latency ölçümü,
    register keşfi ve tip tahmini yapar. Keşfedilen register'lar
    için aday tag'ler oluşturur.
    """

    def __init__(
        self,
        profile: ConnectionProfile,
        database: DatabaseInterface,
    ) -> None:
        self._profile = profile
        self._database = database
        self._client: AsyncModbusTcpClient | None = None

    async def _connect(self) -> AsyncModbusTcpClient:
        """Modbus TCP bağlantısı oluşturur veya mevcut olanı döndürür."""
        if self._client is None:
            self._client = AsyncModbusTcpClient(
                self._profile.host,
                port=self._profile.port,
            )
        if not self._client.connected:
            connected: bool = await self._client.connect()
            if not connected:
                msg = f"Modbus bağlantısı kurulamadı: {self._profile.host}:{self._profile.port}"
                raise ConnectionError(msg)
        return self._client

    async def _disconnect(self) -> None:
        """Modbus TCP bağlantısını kapatır."""
        if self._client is not None:
            self._client.close()
            self._client = None

    async def _update_profile_status(self, status: str, **extra: object) -> None:
        """Connection profile durumunu günceller."""
        if self._profile.id is None:
            return
        updates: dict[str, object] = {"status": status}
        updates.update(extra)
        await self._database.update_connection_profile(self._profile.id, updates)

    async def scan(self) -> list[ScanResult]:
        """Tüm scan adımlarını sırayla çalıştırır.

        1. Slave scan
        2. Latency probing
        3. Register discovery
        4. Type inference
        5. Aday tag oluşturma
        """
        await self._update_profile_status("scanning")
        await logger.ainfo(
            "Scan başlatıldı",
            profil=self._profile.name,
            host=self._profile.host,
            port=self._profile.port,
            unit_id_aralığı=f"{self._profile.unit_id_start}-{self._profile.unit_id_end}",
        )

        try:
            # Adım 1: Slave scan
            active_slaves = await self._scan_slaves()
            if not active_slaves:
                await logger.awarning(
                    "Hiçbir slave cevap vermedi",
                    profil=self._profile.name,
                )
                await self._update_profile_status(
                    "completed",
                    last_scan_at=datetime.now(UTC),
                )
                return []

            await logger.ainfo(
                "Aktif slave'ler bulundu",
                profil=self._profile.name,
                slave_sayısı=len(active_slaves),
                slave_idler=active_slaves,
            )

            # Adım 2: Latency probing (ilk aktif slave için)
            for slave_id in active_slaves:
                min_ms, avg_ms, max_ms = await self._probe_latency(slave_id)
                await logger.ainfo(
                    "Slave latency ölçüldü",
                    slave_id=slave_id,
                    min_ms=round(min_ms, 2),
                    avg_ms=round(avg_ms, 2),
                    max_ms=round(max_ms, 2),
                )

            # Adım 3 + 4: Register discovery + type inference
            all_results: list[ScanResult] = []
            for slave_id in active_slaves:
                addresses = await self._discover_registers(slave_id)
                await logger.ainfo(
                    "Register'lar keşfedildi",
                    slave_id=slave_id,
                    register_sayısı=len(addresses),
                )

                for address, raw_value in addresses:
                    result = await self._infer_type(slave_id, address)
                    # İlk okunan değeri de ekle
                    if raw_value not in result.raw_values:
                        result.raw_values.insert(0, raw_value)
                    all_results.append(result)

            # Adım 5: Aday tag oluşturma
            created_count = await self._create_candidate_tags(all_results)
            await logger.ainfo(
                "Scan tamamlandı",
                profil=self._profile.name,
                toplam_register=len(all_results),
                oluşturulan_tag=created_count,
            )

            await self._update_profile_status(
                "completed",
                last_scan_at=datetime.now(UTC),
            )
            return all_results

        except Exception:
            await logger.aerror(
                "Scan hatası",
                profil=self._profile.name,
                exc_info=True,
            )
            await self._update_profile_status("error")
            return []

        finally:
            await self._disconnect()

    async def _scan_slaves(self) -> list[int]:
        """Unit ID aralığında cevap veren slave'leri bulur."""
        active: list[int] = []
        client = await self._connect()

        for unit_id in range(self._profile.unit_id_start, self._profile.unit_id_end + 1):
            try:
                response = await asyncio.wait_for(
                    client.read_holding_registers(0, count=1, device_id=unit_id),
                    timeout=_READ_TIMEOUT_SEC,
                )
                if not response.isError():
                    active.append(unit_id)
            except (TimeoutError, Exception):
                # Cevap vermeyen slave — devam et
                await logger.adebug(
                    "Slave cevap vermedi",
                    unit_id=unit_id,
                )

        return active

    async def _probe_latency(self, slave_id: int) -> tuple[float, float, float]:
        """Slave için latency ölçümü yapar (min/avg/max ms)."""
        client = await self._connect()
        latencies: list[float] = []

        for _ in range(_LATENCY_PROBE_COUNT):
            start = time.perf_counter()
            try:
                response = await asyncio.wait_for(
                    client.read_holding_registers(0, count=1, device_id=slave_id),
                    timeout=_READ_TIMEOUT_SEC,
                )
                elapsed_ms = (time.perf_counter() - start) * 1000
                if not response.isError():
                    latencies.append(elapsed_ms)
            except (TimeoutError, Exception):
                pass

        if not latencies:
            return (0.0, 0.0, 0.0)

        min_ms = min(latencies)
        avg_ms = sum(latencies) / len(latencies)
        max_ms = max(latencies)

        # Latency'yi profile'a kaydet
        if self._profile.id is not None:
            await self._database.update_connection_profile(
                self._profile.id,
                {
                    "slave_latency_min_ms": min_ms,
                    "slave_latency_avg_ms": avg_ms,
                    "slave_latency_max_ms": max_ms,
                },
            )

        return (min_ms, avg_ms, max_ms)

    async def _discover_registers(
        self,
        slave_id: int,
    ) -> list[tuple[int, int]]:
        """Holding register'ları tarar, geçerli veri döndürenleri kayıt eder."""
        client = await self._connect()
        found: list[tuple[int, int]] = []

        for address in range(_REGISTER_SCAN_START, _REGISTER_SCAN_END + 1):
            try:
                response = await asyncio.wait_for(
                    client.read_holding_registers(
                        address,
                        count=1,
                        device_id=slave_id,
                    ),
                    timeout=_READ_TIMEOUT_SEC,
                )
                if not response.isError():
                    raw_value: int = response.registers[0]
                    found.append((address, raw_value))
            except (TimeoutError, Exception):
                pass

        return found

    async def _infer_type(
        self,
        slave_id: int,
        address: int,
    ) -> ScanResult:
        """Register'ın veri tipini örneklerden tahmin eder."""
        client = await self._connect()
        samples: list[int] = []

        for _ in range(_TYPE_INFERENCE_SAMPLE_COUNT):
            try:
                response = await asyncio.wait_for(
                    client.read_holding_registers(
                        address,
                        count=1,
                        device_id=slave_id,
                    ),
                    timeout=_READ_TIMEOUT_SEC,
                )
                if not response.isError():
                    samples.append(response.registers[0])
            except (TimeoutError, Exception):
                pass

            await asyncio.sleep(_SAMPLE_DELAY_SEC)

        # Tip tahmini
        inferred_type = "uint16"
        stability_note = "stabil"

        if samples:
            min_val = min(samples)
            max_val = max(samples)
            spread = max_val - min_val

            # Büyük değerler int16 olabilir (negatif temsili)
            if any(v > 32767 for v in samples):
                inferred_type = "int16"

            # Stabilite değerlendirmesi
            if spread == 0:
                stability_note = "sabit"
            elif spread <= 5:
                stability_note = "stabil"
            elif spread <= 50:
                stability_note = "değişken"
            else:
                stability_note = "gürültülü"
        else:
            stability_note = "okunamadı"

        return ScanResult(
            slave_id=slave_id,
            register_address=address,
            raw_values=samples,
            inferred_type=inferred_type,
            byte_order="big",
            stability_note=stability_note,
        )

    async def _create_candidate_tags(self, results: list[ScanResult]) -> int:
        """Keşfedilen register'lar için aday tag'ler oluşturur."""
        created = 0

        # Mevcut tag'leri kontrol et (duplicate önleme)
        existing_tags = await self._database.list_tags()
        existing_keys: set[tuple[str, int, int, int]] = {
            (t.modbus_host, t.modbus_port, t.unit_id, t.register_address) for t in existing_tags
        }

        for result in results:
            key = (
                self._profile.host,
                self._profile.port,
                result.slave_id,
                result.register_address,
            )
            if key in existing_keys:
                await logger.adebug(
                    "Tag zaten mevcut, atlanıyor",
                    adres=result.register_address,
                    slave_id=result.slave_id,
                )
                continue

            # Modbus konvansiyonel adres (40001+)
            display_address = 40001 + result.register_address
            tag_id = f"tag_{self._profile.host}_{result.slave_id}_{display_address}"

            tag = TagRecord(
                tag_id=tag_id,
                name=f"Tag_{display_address}",
                modbus_host=self._profile.host,
                modbus_port=self._profile.port,
                unit_id=result.slave_id,
                register_address=result.register_address,
                register_type=result.inferred_type,
                byte_order=result.byte_order,
                polling_interval_ms=10000,
                polling_preset="slow",
                status="discovered",
            )

            try:
                await self._database.insert_tag(tag)
                created += 1
                existing_keys.add(key)
            except Exception:
                await logger.aerror(
                    "Aday tag oluşturulamadı",
                    tag_id=tag_id,
                    exc_info=True,
                )

        return created
