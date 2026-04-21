"""Disk doluluk telemetrisi ve uyarı tick'i (F11 Paket F).

- ``get_disk_usage`` stdlib ``shutil.disk_usage`` üzerinden senkron okuma yapar;
  belirlenen mount point'in bayt bazlı doluluk/kullanım oranını döndürür.
- ``DiskMonitor`` 5 dakikalık asyncio tick döngüsü ile diski ölçer; kullanım
  ``ALERT_THRESHOLD_PERCENT`` (%85) üstüne çıkarsa Web Push bildirimi (severity
  ``warn``) gönderir. Spam olmaması için ``ALERT_COOLDOWN_SECONDS`` (6 saat)
  cooldown bellekte tutulur.

Bu modül ``custos.analytics.push_sender`` import eder; bu yüzden sadece
analytics süreci tarafında kullanılır — critical loop'a ML veya dashboard
bağımlılığı sızmaz.
"""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime

import structlog

from custos.analytics.push_sender import send_push_notifications
from custos.shared.database import DatabaseInterface

logger = structlog.get_logger(logger_name="disk_telemetry")

# Varsayılan mount point — pilot deploy'da /var/custos burada PostgreSQL
# tablespace'i + Parquet arşivleri yer alır. Linux dışı ortamda (dev makine)
# path mevcut değilse shutil FileNotFoundError fırlatır; caller yakalar.
DEFAULT_MOUNT_POINT = "/var/custos"

# Uyarı eşiği (%) — altyapı vizyon özeti §2.3 ile uyumlu. Kullanıcıya önce
# zamanı olsun; bu eşik disk tükenmeden ciddi bir alandır.
ALERT_THRESHOLD_PERCENT = 85.0

# İki uyarı arasında minimum süre (saniye) — 6 saat, dezenfekte ettirmeden
# operatörü bıktırmamak için.
ALERT_COOLDOWN_SECONDS = 6 * 3600

# DiskMonitor default tick — 5 dakika. Kısa yaparsak disk I/O sık, uzun
# yaparsak uyarı gecikir. 5 dk eşikte sadece 5 dk gecikme kabul edilebilir.
DEFAULT_TICK_SECONDS = 300


@dataclass(frozen=True)
class DiskUsage:
    """Tek bir mount point'in bayt cinsinden doluluk bilgisi.

    ``used_percent`` 0-100 aralığında float; ``shutil`` tam sayı bayt döner
    ama yüzdeyi float tutuyoruz ki UI bar'ı yumuşak gözüksün.
    """

    mount_point: str
    total_bytes: int
    used_bytes: int
    free_bytes: int
    used_percent: float


def get_disk_usage(path: str = DEFAULT_MOUNT_POINT) -> DiskUsage:
    """Verilen mount point için disk kullanımını döndürür.

    ``shutil.disk_usage`` senkrondur; çağıran asyncio loop'undan bu fonksiyonu
    ``asyncio.to_thread`` ile sarmalamalıdır (tick içinde zaten öyle
    yapılıyor).
    """
    total, used, free = shutil.disk_usage(path)
    used_pct = (used / total * 100.0) if total > 0 else 0.0
    return DiskUsage(
        mount_point=path,
        total_bytes=total,
        used_bytes=used,
        free_bytes=free,
        used_percent=used_pct,
    )


class DiskMonitor:
    """5 dakikalık tick loop — disk %85'i geçince push notification.

    Cooldown in-memory tutulur; süreç yeniden başlarsa ilk tick'te tekrar
    uyarı gidebilir. Pilot için kabul edilebilir — operatör zaten restart
    olayını görür.
    """

    def __init__(
        self,
        db: DatabaseInterface,
        mount_point: str = DEFAULT_MOUNT_POINT,
        tick_seconds: int = DEFAULT_TICK_SECONDS,
        threshold_percent: float = ALERT_THRESHOLD_PERCENT,
        cooldown_seconds: int = ALERT_COOLDOWN_SECONDS,
    ) -> None:
        self._db = db
        self._mount_point = mount_point
        self._tick_seconds = tick_seconds
        self._threshold = threshold_percent
        self._cooldown = cooldown_seconds
        self._running = False
        self._last_alert_at: datetime | None = None

    @property
    def last_alert_at(self) -> datetime | None:
        """Test ve diagnostik için son uyarı zamanı."""
        return self._last_alert_at

    async def start(self) -> None:
        """Arka plan döngüsü — süreç boyunca çalışır."""
        self._running = True
        await logger.ainfo(
            "Disk monitor başlatıldı",
            mount_point=self._mount_point,
            tick_seconds=self._tick_seconds,
            threshold_percent=self._threshold,
        )
        try:
            while self._running:
                try:
                    await self.run_once()
                except FileNotFoundError:
                    # Pilot deploy'unda /var/custos yoksa veya dev ortamında
                    # mount point eksikse log + sessizce geç. Tekrar deneriz.
                    await logger.awarning(
                        "Disk monitor — mount point bulunamadı",
                        mount_point=self._mount_point,
                    )
                except Exception:
                    await logger.aerror(
                        "Disk monitor tick hatası", exc_info=True,
                    )
                await asyncio.sleep(self._tick_seconds)
        except asyncio.CancelledError:
            await logger.ainfo("Disk monitor iptal edildi")

    async def stop(self) -> None:
        """Döngüyü durdurur."""
        self._running = False
        await logger.ainfo("Disk monitor durduruldu")

    async def run_once(self) -> DiskUsage:
        """Tek tick — disk ölç, eşik aşıldıysa cooldown kontrolü ile push at.

        Test ve manuel tetikleme için dışarıdan da çağrılabilir. Ölçülen
        ``DiskUsage``'ı döndürür (çağıran log'a yazabilir).
        """
        usage = await asyncio.to_thread(get_disk_usage, self._mount_point)
        if usage.used_percent >= self._threshold:
            await self._maybe_send_alert(usage)
        return usage

    async def _maybe_send_alert(self, usage: DiskUsage) -> None:
        """Cooldown kontrolünden sonra push notification tetikle."""
        now = datetime.now(UTC)
        if self._last_alert_at is not None:
            elapsed = (now - self._last_alert_at).total_seconds()
            if elapsed < self._cooldown:
                return
        title = "Disk doluluğu yüksek"
        pct = round(usage.used_percent, 1)
        body = (
            f"Disk %{pct:.1f} doldu. Settings → Veri Saklama'dan retention "
            f"süresini kısaltabilir veya eski ayları arşivleyebilirsiniz."
        )
        await send_push_notifications(
            db=self._db, title=title, body=body, severity="warn",
        )
        self._last_alert_at = now
        await logger.ainfo(
            "Disk doluluk uyarısı gönderildi",
            used_percent=round(usage.used_percent, 2),
            threshold_percent=self._threshold,
            mount_point=self._mount_point,
        )
