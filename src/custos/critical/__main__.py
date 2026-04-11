"""Critical loop entry point.

Kullanım: python -m custos.critical

Collector'ı başlatır: veritabanından aktif tag'leri yükler
ve Modbus okuma döngüsünü çalıştırır.
"""

from __future__ import annotations

import asyncio
import signal

import structlog

from custos.critical.collector import ModbusCollector
from custos.shared.config import settings
from custos.shared.database import create_database
from custos.shared.logging import configure_logging

logger = structlog.get_logger(logger_name="critical")


async def main() -> None:
    """Critical loop ana fonksiyonu."""
    configure_logging(settings.log_level)

    # Veritabanı bağlantısı
    database = create_database(settings)
    await database.connect()

    # Aktif tag'leri DB'den yükle
    tags = await database.list_tags(status="active")
    if not tags:
        await logger.awarning("Aktif tag bulunamadı — collector başlatılmıyor")
        await database.close()
        return

    await logger.ainfo("Tag'ler yüklendi", tag_sayısı=len(tags))

    # Collector oluştur
    collector = ModbusCollector(tags=tags, database=database)

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
