"""Critical loop entry point.

Kullanım: python -m custos.critical

Collector'ı başlatır: veritabanından aktif tag'leri yükler
ve Modbus okuma döngüsünü çalıştırır.

Watchdog (V11-105/K13):
- systemd Type=notify altında her 30 sn ``WATCHDOG=1`` gönderir.
- Her 60 sn DB'ye ``service_heartbeats`` upsert'i yapar
  (cross-service kontrolü analytics loop tarafında).
"""

from __future__ import annotations

import asyncio
import signal

import structlog

from custos.analytics.heartbeat import write_heartbeat
from custos.critical.collector import FastPollingBudgetError, ModbusCollector
from custos.critical.threshold_watcher import ThresholdWatcher
from custos.shared.config import settings
from custos.shared.database import DatabaseInterface, create_database
from custos.shared.logging import configure_logging
from custos.shared.watchdog import SystemdWatchdog

logger = structlog.get_logger(logger_name="critical")

# Cross-service heartbeat aralığı — analytics CRIT_THRESHOLD (180s) ile
# uyumlu güvenli üst sınır.
HEARTBEAT_INTERVAL_SECONDS: float = 60.0


async def _heartbeat_loop(db: DatabaseInterface, service_name: str) -> None:
    """DB'ye periyodik heartbeat yazar — cross-service watchdog için."""
    while True:
        await write_heartbeat(db, service_name)
        try:
            await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            break


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

    # Collector oluştur — Settings'ten config parametrelerini geçir.
    try:
        collector = ModbusCollector(
            tags=tags,
            database=database,
            per_host_concurrency=settings.collector_per_host_concurrency,
            fast_polling_budget=settings.collector_fast_polling_budget,
            batch_read_enabled=settings.collector_batch_read_enabled,
            batch_gap_tolerance=settings.collector_batch_gap_tolerance,
        )
    except FastPollingBudgetError:
        await logger.aerror(
            "Collector başlatılamadı: fast polling bütçesi aşıldı",
            exc_info=True,
        )
        await database.close()
        return

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

    # Watchdog setup — systemd Type=notify + cross-service heartbeat.
    watchdog = SystemdWatchdog(interval_seconds=30.0)
    watchdog.notify_ready()
    sd_task = asyncio.create_task(watchdog.heartbeat_loop())
    hb_task = asyncio.create_task(_heartbeat_loop(database, "custos-critical"))

    # Threshold watcher (review H1) — eşik tabanlı alarm üretimi artık Critical
    # loop'ta. Collector ile aynı event loop'ta ayrı task; son okumaları
    # Collector'dan in-memory alır, alarm yazar (push YOK — Analytics dispatch eder).
    watcher = ThresholdWatcher(db=database, reading_source=collector.latest_readings)
    watcher_task = asyncio.create_task(watcher.start())

    try:
        await collector.start()
    finally:
        watchdog.notify_stopping()
        await watcher.stop()
        for task in (sd_task, hb_task, watcher_task):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await collector.stop()
        await database.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
