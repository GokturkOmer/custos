"""CPU + RAM telemetri ve uyari tick'i (V11-111 / P-06).

DiskMonitor pattern'i (``analytics/disk_telemetry.py``) baz alindi:

- 60 saniyede bir ``psutil`` ile CPU yuzdesi + RAM yuzdesi sample alinir.
- Son 5 sample (= 5 dk) deque buffer'da tutulur.
- Buffer dolu ve mean >= esik (default %90) ise push (severity=``warn``).
- Esikler ``retention_config`` tablosundan her tick'te okunur — Settings UI'da
  override edilebilir (V11-111 deliverable, range 50-99 CHECK constraint).
- 6 saat in-memory cooldown; CPU ve RAM her biri icin ayri sayilir
  (kucuk farkliliklar tek bir tetik fazina uretmesin).

``psutil`` pure-Python paket olarak ``pyproject.toml`` dependency'lerinde
explicit listelendi (P-06). Endurance metrik script'leri zaten transitive
olarak kullaniyordu.

Bu modul ``custos.analytics.push_sender`` import eder; sadece analytics
sureci tarafinda kullanilir. Critical loop'a sizmaz.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime

import psutil
import structlog

from custos.analytics.push_sender import send_push_notifications
from custos.shared.database import DatabaseInterface

logger = structlog.get_logger(logger_name="resource_telemetry")

# Tick aralig (saniye) — 60 sn. WINDOW_SAMPLES * tick_seconds = pencere uzunlugu.
DEFAULT_TICK_SECONDS = 60.0

# Pencere uzunlugu (sample sayisi). 60s × 5 = 5 dk — V11-111 spec.
WINDOW_SAMPLES = 5

# Iki uyari arasi minimum sure. Disk telemetri ile ayni — operatoru sik
# tetiklenen uyari ile bezdirme.
ALERT_COOLDOWN_SECONDS = 6 * 3600


@dataclass(frozen=True)
class ResourceSample:
    """Tek tick'te alinan CPU + RAM sample'i.

    ``cpu_percent`` 0-100 araliginda float (multi-core sistemde toplam yuk).
    ``ram_percent`` ``psutil.virtual_memory().percent`` — kullanim/total.
    """

    timestamp: datetime
    cpu_percent: float
    ram_percent: float


def get_resource_sample() -> ResourceSample:
    """psutil ile anlik CPU + RAM yuzdesi alir.

    ``psutil.cpu_percent(interval=None)`` son cagrildigi noktadan beri olcum
    doner; ilk cagrida 0.0 olabilir. ``ResourceMonitor.start`` baslamadan
    once bir warm-up cagrisi yapar.
    """
    return ResourceSample(
        timestamp=datetime.now(UTC),
        cpu_percent=psutil.cpu_percent(interval=None),
        ram_percent=psutil.virtual_memory().percent,
    )


class ResourceMonitor:
    """5 dakikalik mean — esik asimi durumunda push (severity=warn).

    Cooldown in-memory tutulur; surec restart olunca ilk tick'te tekrar
    uyari gidebilir (DiskMonitor ile ayni kabul — operator zaten restart
    olayini gorur).

    CPU ve RAM bagimsiz buffer + bagimsiz cooldown. Aralarinda korelasyon
    yok diye varsayiyoruz; gercek pilot saha verisi gosterirse refactor.
    """

    def __init__(
        self,
        db: DatabaseInterface,
        tick_seconds: float = DEFAULT_TICK_SECONDS,
        cooldown_seconds: float = ALERT_COOLDOWN_SECONDS,
        window_samples: int = WINDOW_SAMPLES,
    ) -> None:
        self._db = db
        self._tick_seconds = tick_seconds
        self._cooldown = cooldown_seconds
        self._cpu_buffer: deque[float] = deque(maxlen=window_samples)
        self._ram_buffer: deque[float] = deque(maxlen=window_samples)
        self._last_cpu_alert_at: datetime | None = None
        self._last_ram_alert_at: datetime | None = None
        self._running = False

    @property
    def cpu_buffer(self) -> tuple[float, ...]:
        """Test ve diagnostik icin son CPU sample'lar."""
        return tuple(self._cpu_buffer)

    @property
    def ram_buffer(self) -> tuple[float, ...]:
        """Test ve diagnostik icin son RAM sample'lar."""
        return tuple(self._ram_buffer)

    @property
    def last_cpu_alert_at(self) -> datetime | None:
        """Son CPU alarminin gonderildigi an (cooldown takibi)."""
        return self._last_cpu_alert_at

    @property
    def last_ram_alert_at(self) -> datetime | None:
        """Son RAM alarminin gonderildigi an (cooldown takibi)."""
        return self._last_ram_alert_at

    async def start(self) -> None:
        """Arka plan dongusu — surec boyunca calisir."""
        self._running = True
        # psutil.cpu_percent ilk cagride 0 dondurebilir — warm-up.
        psutil.cpu_percent(interval=None)
        await logger.ainfo(
            "Resource monitor baslatildi",
            tick_seconds=self._tick_seconds,
            window_samples=self._cpu_buffer.maxlen,
            cooldown_seconds=self._cooldown,
        )
        try:
            while self._running:
                try:
                    await self.run_once()
                except Exception:
                    await logger.aerror(
                        "Resource monitor tick hatasi",
                        exc_info=True,
                    )
                await asyncio.sleep(self._tick_seconds)
        except asyncio.CancelledError:
            await logger.ainfo("Resource monitor iptal edildi")

    async def stop(self) -> None:
        """Donguyu durdurur."""
        self._running = False
        await logger.ainfo("Resource monitor durduruldu")

    async def run_once(self) -> ResourceSample:
        """Tek tick — sample al, buffer'a ekle, esik kontrol et, push.

        Test ve manuel tetikleme icin disaridan cagrilabilir.
        """
        sample = await asyncio.to_thread(get_resource_sample)
        self._cpu_buffer.append(sample.cpu_percent)
        self._ram_buffer.append(sample.ram_percent)

        # Esikleri DB'den taze oku — Settings'te override edilince bir sonraki
        # tick'te etkili olur (1 dk gecikme kabul edilebilir).
        config = await self._db.get_retention_config()
        cpu_threshold = float(config.resource_cpu_warn_pct)
        ram_threshold = float(config.resource_ram_warn_pct)

        # Buffer 5 sample'a doldugunda mean hesapla; eksikse uyari yok.
        if len(self._cpu_buffer) >= (self._cpu_buffer.maxlen or 0):
            cpu_mean = sum(self._cpu_buffer) / len(self._cpu_buffer)
            if cpu_mean >= cpu_threshold:
                await self._maybe_send_alert(
                    resource="cpu",
                    mean_percent=cpu_mean,
                    threshold=cpu_threshold,
                )
        if len(self._ram_buffer) >= (self._ram_buffer.maxlen or 0):
            ram_mean = sum(self._ram_buffer) / len(self._ram_buffer)
            if ram_mean >= ram_threshold:
                await self._maybe_send_alert(
                    resource="ram",
                    mean_percent=ram_mean,
                    threshold=ram_threshold,
                )
        return sample

    async def _maybe_send_alert(
        self,
        resource: str,
        mean_percent: float,
        threshold: float,
    ) -> None:
        """Cooldown kontrolu ardindan push notification tetikler."""
        now = datetime.now(UTC)
        last = (
            self._last_cpu_alert_at if resource == "cpu" else self._last_ram_alert_at
        )
        if last is not None:
            elapsed = (now - last).total_seconds()
            if elapsed < self._cooldown:
                return

        title = "CPU yuksek" if resource == "cpu" else "RAM yuksek"
        body = (
            f"Son 5 dakika ortalamasi %{mean_percent:.1f} "
            f"(esik %{threshold:.0f}). Ust uste yuksek kalirsa pilot "
            f"performansi etkilenebilir."
        )
        await send_push_notifications(
            db=self._db,
            title=title,
            body=body,
            severity="warn",
        )
        if resource == "cpu":
            self._last_cpu_alert_at = now
        else:
            self._last_ram_alert_at = now
        await logger.ainfo(
            "Resource alarmi gonderildi",
            resource=resource,
            mean_percent=round(mean_percent, 2),
            threshold=threshold,
        )
