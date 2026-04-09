"""Sahte Modbus TCP server.

5 endüstriyel sensörü simüle eden holding register'lar sunar.
Değerler random walk ile güncellenir.
"""

from __future__ import annotations

import asyncio
import random
import signal
from typing import Any

import structlog
from pymodbus.datastore import (
    ModbusDeviceContext,
    ModbusSequentialDataBlock,
    ModbusServerContext,
)
from pymodbus.server import StartAsyncTcpServer

logger = structlog.get_logger(logger_name="simulator")

# Sensör tanımları: (register, başlangıç, min, max, adım)
# P001 ve V001 ölçekli (×10), bu yüzden register değeri gerçek ×10
SENSOR_DEFS: list[tuple[int, int, int, int, int]] = [
    (0, 50, 20, 90, 1),  # T001: sıcaklık, celsius
    (1, 50, 0, 100, 2),  # P001: basınç, bar×10 (0-100 → 0-10 bar)
    (2, 250, 0, 500, 10),  # F001: debi, m³/saat
    (3, 125, 0, 250, 5),  # V001: titreşim, mm/s×10 (0-250 → 0-25 mm/s)
    (4, 1500, 0, 3000, 50),  # R001: devir, RPM
]

# Holding register function code
_HR_FC = 3


class ModbusSimulator:
    """Sahte Modbus TCP server.

    Belirtilen host:port üzerinde Modbus TCP dinler ve
    holding register'lardaki değerleri periyodik olarak günceller.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 5020) -> None:
        self._host = host
        self._port = port
        self._shutdown_event = asyncio.Event()
        self._update_count = 0
        self._context: ModbusServerContext | None = None

    def _create_context(self) -> ModbusServerContext:
        """Modbus server context oluşturur."""
        # 5 register (0-4), başlangıç değerleri
        # pymodbus ModbusSequentialDataBlock'ta ilk eleman iç indeks olarak
        # kullanıldığından, adres 0-4 için 6 elemanlı liste gerekir.
        initial_values = [0] * 6
        for reg, start, _mn, _mx, _step in SENSOR_DEFS:
            initial_values[reg + 1] = start

        block = ModbusSequentialDataBlock(0, initial_values)  # type: ignore[no-untyped-call]
        store = ModbusDeviceContext(hr=block)
        return ModbusServerContext(devices=store, single=True)  # type: ignore[no-untyped-call]

    async def _update_values(self) -> None:
        """Register değerlerini random walk ile günceller (500ms aralıkla)."""
        assert self._context is not None
        store: ModbusDeviceContext = self._context[0]

        while not self._shutdown_event.is_set():
            for reg, _start, mn, mx, step in SENSOR_DEFS:
                current_values = store.getValues(_HR_FC, reg, 1)
                if not isinstance(current_values, list):
                    continue
                current = current_values[0]
                delta = random.randint(-step, step)
                new_value = max(mn, min(mx, current + delta))
                store.setValues(_HR_FC, reg, [new_value])

            self._update_count += 1

            # Her 20 güncellemede (10 saniye) özet log
            if self._update_count % 20 == 0:
                values = store.getValues(_HR_FC, 0, 5)
                await logger.ainfo(
                    "Simülatör değer özeti",
                    güncelleme_sayısı=self._update_count,
                    register_değerleri=values,
                )

            try:
                await asyncio.wait_for(
                    self._shutdown_event.wait(),
                    timeout=0.5,
                )
                # Shutdown sinyali geldi
                break
            except TimeoutError:
                pass

    async def start(self) -> None:
        """Simülatörü başlatır: server + değer güncelleme."""
        self._context = self._create_context()
        self._shutdown_event.clear()

        await logger.ainfo(
            "Modbus simülatör başlatılıyor",
            host=self._host,
            port=self._port,
        )

        # Değer güncelleme task'ını başlat
        update_task = asyncio.create_task(self._update_values())

        try:
            await StartAsyncTcpServer(
                context=self._context,
                address=(self._host, self._port),
            )
        except asyncio.CancelledError:
            await logger.ainfo("Modbus simülatör durduruluyor")
        finally:
            self._shutdown_event.set()
            await update_task

        await logger.ainfo(
            "Modbus simülatör durduruldu",
            toplam_güncelleme=self._update_count,
        )

    def stop(self) -> None:
        """Simülatörü durdurma sinyali gönderir."""
        self._shutdown_event.set()


async def main() -> None:
    """Simülatör entry point."""
    from custos.shared.logging import configure_logging

    configure_logging("INFO")

    simulator = ModbusSimulator()

    loop = asyncio.get_running_loop()

    # Graceful shutdown signal handler'ları
    def _signal_handler(*_args: Any) -> None:
        simulator.stop()
        # Tüm task'ları iptal et (server dahil)
        for task in asyncio.all_tasks(loop):
            task.cancel()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            # Windows'ta signal handler desteklenmeyebilir
            signal.signal(sig, _signal_handler)

    try:
        await simulator.start()
    except asyncio.CancelledError:
        pass

    await logger.ainfo("Simülatör temiz kapandı")
