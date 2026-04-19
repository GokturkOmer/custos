"""Sahte Modbus TCP server.

30 AVM sensörünü simüle eden holding register'lar sunar.
Değerler time-based pattern motoru ile saniyelik güncellenir:
    - 24 saatlik diurnal sinüs
    - AVM açık saatlerinde (09–22) boost
    - Zamanlı anomaliler (spike, dropout, bearing wear)

Kayıt haritası için src/custos/simulator/sensors.py dosyasına bakın.
"""

from __future__ import annotations

import asyncio
import random
import signal
from datetime import UTC, datetime
from typing import Any

import structlog
from pymodbus.datastore import (
    ModbusDeviceContext,
    ModbusSequentialDataBlock,
    ModbusServerContext,
)
from pymodbus.server import StartAsyncTcpServer

from custos.simulator.patterns import (
    Anomaly,
    SensorPattern,
    anomaly_delta,
    compute_base_value,
)
from custos.simulator.sensors import SENSORS, SensorDef

logger = structlog.get_logger(logger_name="simulator")

# Holding register function code
_HR_FC = 3
# Register değer güncelleme periyodu (saniye). Polling 1 Hz ise bu yeter.
_UPDATE_INTERVAL_SEC = 0.5


def _value_to_register(value: float, sensor: SensorDef) -> int:
    """Gerçek fiziksel değeri register uint16 değerine çevirir."""
    raw = (value - sensor.offset) / sensor.gain
    # uint16 aralığına clamp
    if raw < 0:
        raw = 0
    elif raw > 65535:
        raw = 65535
    return int(round(raw))


def _register_to_value(register: int, sensor: SensorDef) -> float:
    """Register değerini gerçek fiziksel değere çevirir."""
    return register * sensor.gain + sensor.offset


def compute_sensor_register(
    sensor: SensorDef,
    now: datetime,
    sim_start: datetime,
    noise: float,
) -> int:
    """Bir sensör için anlık register değeri (pattern + anomali + gürültü)."""
    value = compute_base_value(sensor.pattern, now)
    if sensor.anomaly is not None:
        value += anomaly_delta(sensor.anomaly, now, sim_start)
    if sensor.pattern.noise_amp > 0:
        value += noise * sensor.pattern.noise_amp
    if sensor.pattern.min_value is not None and value < sensor.pattern.min_value:
        value = sensor.pattern.min_value
    if sensor.pattern.max_value is not None and value > sensor.pattern.max_value:
        value = sensor.pattern.max_value
    return _value_to_register(value, sensor)


class ModbusSimulator:
    """AVM pilotu için 30 sensörlü sahte Modbus TCP server.

    Belirtilen host:port üzerinde Modbus TCP dinler ve tüm register'ları
    sensors.py'daki pattern + anomali tanımlarına göre günceller.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 5020) -> None:
        self._host = host
        self._port = port
        self._shutdown_event = asyncio.Event()
        self._update_count = 0
        self._context: ModbusServerContext | None = None
        self._sim_start: datetime = datetime.now(UTC)
        self._rng = random.Random(42)  # deterministic gürültü seed

    def _register_count(self) -> int:
        """Sequential block boyutu. En yüksek register + 1 eleman gerek."""
        return max(s.register for s in SENSORS) + 1

    def _create_context(self) -> ModbusServerContext:
        """Modbus server context oluşturur; başlangıç değerlerini pattern'den üretir."""
        now = self._sim_start
        count = self._register_count()
        initial: list[int] = [0] * count
        for sensor in SENSORS:
            initial[sensor.register] = compute_sensor_register(
                sensor, now, self._sim_start, noise=0.0
            )

        block = ModbusSequentialDataBlock(0, initial)  # type: ignore[no-untyped-call]
        store = ModbusDeviceContext(hr=block)
        return ModbusServerContext(devices=store, single=True)  # type: ignore[no-untyped-call]

    async def _update_values(self) -> None:
        """Tüm register'ları pattern + anomali ile periyodik günceller."""
        assert self._context is not None
        store: ModbusDeviceContext = self._context[0]

        while not self._shutdown_event.is_set():
            now = datetime.now(UTC)
            for sensor in SENSORS:
                noise = self._rng.gauss(0.0, 1.0)
                new_reg = compute_sensor_register(
                    sensor, now, self._sim_start, noise
                )
                store.setValues(_HR_FC, sensor.register, [new_reg])

            self._update_count += 1

            # Her 60 güncellemede (≈30 s) özet log — seçili birkaç sensör
            if self._update_count % 60 == 0:
                sample = [SENSORS[0], SENSORS[6], SENSORS[10], SENSORS[24]]
                summary: dict[str, float] = {}
                for s in sample:
                    raw = store.getValues(_HR_FC, s.register, 1)
                    if isinstance(raw, list) and raw:
                        summary[s.tag_id] = round(
                            _register_to_value(int(raw[0]), s), 2
                        )
                await logger.ainfo(
                    "Simülatör değer özeti",
                    güncelleme_sayısı=self._update_count,
                    örnek=summary,
                )

            try:
                await asyncio.wait_for(
                    self._shutdown_event.wait(),
                    timeout=_UPDATE_INTERVAL_SEC,
                )
                break
            except TimeoutError:
                pass

    async def start(self) -> None:
        """Simülatörü başlatır: server + değer güncelleme."""
        self._sim_start = datetime.now(UTC)
        self._context = self._create_context()
        self._shutdown_event.clear()

        await logger.ainfo(
            "Modbus simülatör başlatılıyor",
            host=self._host,
            port=self._port,
            sensör_sayısı=len(SENSORS),
        )

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


# Geriye dönük uyumluluk: eski test'ler veya dış kod için export
__all__ = [
    "Anomaly",
    "ModbusSimulator",
    "SensorDef",
    "SensorPattern",
    "compute_sensor_register",
    "main",
]


async def main() -> None:
    """Simülatör entry point."""
    from custos.shared.logging import configure_logging

    configure_logging("INFO")

    simulator = ModbusSimulator()
    loop = asyncio.get_running_loop()

    def _signal_handler(*_args: Any) -> None:
        simulator.stop()
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
