"""Parquet arşiv cron-benzeri scheduler (F11 Paket E).

Maintenance scheduler pattern'ine paralel bir asyncio tick döngüsü. Her ayın
1'inde 02:00 TRT'ye gelindiğinde bir önceki ayı arşivler. 02:00 seçimi pilot
saha için yoğun saatler dışı — batch yazma ve disk I/O kullanıcının aktif
kullanımına çarpmasın.

Neden APScheduler değil: proje hâlihazırda ``asyncio.create_task`` tabanlı
scheduler'lar kullanıyor (bkz. ``MaintenanceScheduler``). Tek satır cron
ifadesi için yeni bir bağımlılık eklemek çok fazla yük; aynı pattern bu job
için de yeterli.

Kilit: ``dashboard.app._archive_lock`` ile paylaşılır — manuel endpoint veya
test aynı anda tetiklenirse iki kez yazım olmaz.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import structlog

from custos.analytics.archiver import ParquetArchiver

logger = structlog.get_logger(logger_name="archive_scheduler")

# TRT — takvim ayı ve 02:00 sabitini bu zaman diliminde hesaplıyoruz.
_TRT = ZoneInfo("Europe/Istanbul")

# Varsayılan tick periyodu — 5 dakika, maintenance scheduler ile aynı.
# Daha kısa yapmanın anlamı yok: hedef zaman penceresi 24 saat içinde 1 kez.
DEFAULT_TICK_SECONDS = 300

# Arşiv hedef saati (TRT) — her ayın 1'inde bu saatte çalışır.
_RUN_HOUR_TRT = 2
_RUN_MINUTE_TRT = 0


def _compute_next_run_utc(reference: datetime) -> datetime:
    """Verilen UTC referansından sonraki "ayın 1'i 02:00 TRT"yi UTC olarak döner.

    Eğer bu ay henüz 1'inin 02:00 TRT'sine gelinmediyse bu ayın 1'i dönebilir;
    geçilmişse bir sonraki ayın 1'i döner. Scheduler her tick'te bu hedefi
    alır, ``now >= next_run`` ise çalıştırır ve tekrar hesaplar.
    """
    local = reference.astimezone(_TRT)
    this_month_first = local.replace(
        day=1,
        hour=_RUN_HOUR_TRT,
        minute=_RUN_MINUTE_TRT,
        second=0,
        microsecond=0,
    )
    if local < this_month_first:
        return this_month_first.astimezone(UTC)
    # Bir sonraki ayın 1'i 02:00 TRT
    if local.month == 12:
        next_month_first = this_month_first.replace(
            year=local.year + 1,
            month=1,
        )
    else:
        next_month_first = this_month_first.replace(month=local.month + 1)
    return next_month_first.astimezone(UTC)


class ArchiveScheduler:
    """Her ayın 1'inde 02:00 TRT'de ``run_scheduled`` tetikleyen tick döngüsü."""

    def __init__(
        self,
        archiver: ParquetArchiver,
        lock: asyncio.Lock,
        tick_seconds: int = DEFAULT_TICK_SECONDS,
    ) -> None:
        self._archiver = archiver
        self._lock = lock
        self._tick_seconds = tick_seconds
        self._running = False
        self._next_run_utc: datetime | None = None

    @property
    def next_run_utc(self) -> datetime | None:
        """Bir sonraki planlanmış çalışma zamanı (test / diagnostik amaçlı)."""
        return self._next_run_utc

    async def start(self) -> None:
        """Arka plan döngüsü — süreç boyunca çalışır."""
        self._running = True
        self._next_run_utc = _compute_next_run_utc(datetime.now(UTC))
        await logger.ainfo(
            "Archive scheduler başlatıldı",
            tick_seconds=self._tick_seconds,
            next_run_utc=self._next_run_utc.isoformat(),
        )
        try:
            while self._running:
                try:
                    await self.run_once()
                except Exception:
                    await logger.aerror(
                        "Archive scheduler tick hatası",
                        exc_info=True,
                    )
                await asyncio.sleep(self._tick_seconds)
        except asyncio.CancelledError:
            await logger.ainfo("Archive scheduler iptal edildi")

    async def stop(self) -> None:
        """Döngüyü durdurur."""
        self._running = False
        await logger.ainfo("Archive scheduler durduruldu")

    async def run_once(self) -> None:
        """Tek tick — hedef saate gelindiyse arşivle ve bir sonraki ay'a ilerle.

        Test ve manuel tetikleme için dışarıdan da çağrılabilir.
        """
        now = datetime.now(UTC)
        if self._next_run_utc is None:
            self._next_run_utc = _compute_next_run_utc(now)
            return
        if now < self._next_run_utc:
            return
        if self._lock.locked():
            # Manuel endpoint aynı anda çalışıyorsa bu tick'i atla, bir sonrakinde
            # tekrar denenir. Kilit alamadık diye next_run'ı kaydırmıyoruz.
            await logger.awarning(
                "Scheduled arşiv atlandı: kilit başka bir iş tarafından tutuluyor",
            )
            return
        async with self._lock:
            try:
                result = await self._archiver.run_scheduled()
                await logger.ainfo(
                    "Scheduled arşiv tamamlandı",
                    year=result.year,
                    month=result.month,
                    duration_seconds=round(result.duration_seconds, 3),
                )
            finally:
                # Başarılı/başarısız fark etmez — bir sonraki pencereyi kaydır ki
                # sonsuz retry döngüsüne girmeyelim. Başarısız arşiv log'a düşer,
                # operatör manuel endpoint ile tekrar dener.
                self._next_run_utc = _compute_next_run_utc(
                    self._next_run_utc + timedelta(seconds=1),
                )
                await logger.ainfo(
                    "Bir sonraki arşiv zamanı güncellendi",
                    next_run_utc=self._next_run_utc.isoformat(),
                )
