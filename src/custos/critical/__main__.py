"""Critical loop entry point.

Kullanım: python -m custos.critical

Collector'ı başlatır: sensör konfigürasyonunu yükler,
veritabanına bağlanır ve Modbus okuma döngüsünü çalıştırır.
"""

from __future__ import annotations

import asyncio
import signal
from pathlib import Path

from custos.critical.collector import ModbusCollector
from custos.shared.config import settings
from custos.shared.database import create_database
from custos.shared.logging import configure_logging
from custos.shared.sensor_config import load_sensor_configs


async def main() -> None:
    """Critical loop ana fonksiyonu."""
    configure_logging(settings.log_level)

    # Sensör konfigürasyonunu yükle
    sensors = load_sensor_configs(Path("config/sensors.toml"))

    # Veritabanı bağlantısı
    database = create_database(settings)
    await database.connect()

    # Collector oluştur
    collector = ModbusCollector(sensors=sensors, database=database)

    # Signal handler kur
    loop = asyncio.get_running_loop()

    def _signal_handler() -> None:
        loop.create_task(collector.stop())

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            # Windows'ta add_signal_handler desteklenmez
            pass

    try:
        await collector.start()
    finally:
        await collector.stop()
        await database.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
