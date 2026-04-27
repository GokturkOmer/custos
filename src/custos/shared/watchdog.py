"""systemd Type=notify + WatchdogSec entegrasyonu (V11-105/K13).

İnce bir wrapper — ``sdnotify`` paketi varsa ve systemd ``WATCHDOG_USEC``
env değişkeni set'liyse heartbeat gönderir; aksi halde no-op döner.

Lokal dev (systemd dışı) ortamda log seviyesi DEBUG kalır, görsel kirlilik
yok. Critical loop import etmek zorunda — bu modül **ML/numeric bağımlılık
içermez**, sadece std-lib + (opsiyonel) sdnotify.
"""

from __future__ import annotations

import asyncio
import os

import structlog

logger = structlog.get_logger(logger_name="watchdog")


def _systemd_active() -> bool:
    """Servis sd_notify protokolüyle çalışıyor mu?

    NOTIFY_SOCKET set'liyse evet (systemd Type=notify ile başlattıysa).
    """
    return bool(os.environ.get("NOTIFY_SOCKET"))


def _watchdog_active() -> bool:
    """systemd WatchdogSec ayarlı mı?

    WATCHDOG_USEC env değişkeni servis dosyasındaki ``WatchdogSec`` ile
    set'lenir; yoksa heartbeat'e gerek yok.
    """
    return bool(os.environ.get("WATCHDOG_USEC"))


class SystemdWatchdog:
    """sd_notify wrapper — periyodik WATCHDOG=1 gönderir.

    Kullanım (lifespan içinde):
        wd = SystemdWatchdog()
        wd.notify_ready()
        task = asyncio.create_task(wd.heartbeat_loop())
        ...
        await wd.stop(task)
    """

    def __init__(self, interval_seconds: float = 30.0) -> None:
        self._interval = interval_seconds
        self._notifier: object | None = None
        self._enabled: bool = False
        if not _systemd_active():
            return
        try:
            from sdnotify import SystemdNotifier

            self._notifier = SystemdNotifier()
            self._enabled = True
        except ImportError:
            # Pakete dokunmadan kurulu değilse (test/lokal) — no-op.
            self._enabled = False

    def notify_ready(self) -> None:
        """systemd'ye servisin hazır olduğunu bildirir (READY=1)."""
        if not self._enabled or self._notifier is None:
            return
        try:
            self._notifier.notify("READY=1")  # type: ignore[attr-defined]
        except Exception:
            logger.warning("sd_notify READY=1 başarısız", exc_info=True)

    def notify_stopping(self) -> None:
        """systemd'ye servisin durmakta olduğunu bildirir (STOPPING=1)."""
        if not self._enabled or self._notifier is None:
            return
        try:
            self._notifier.notify("STOPPING=1")  # type: ignore[attr-defined]
        except Exception:
            logger.warning("sd_notify STOPPING=1 başarısız", exc_info=True)

    def _tick(self) -> None:
        """Tek bir WATCHDOG=1 atımı."""
        if not self._enabled or self._notifier is None:
            return
        try:
            self._notifier.notify("WATCHDOG=1")  # type: ignore[attr-defined]
        except Exception:
            logger.warning("sd_notify WATCHDOG=1 başarısız", exc_info=True)

    async def heartbeat_loop(self) -> None:
        """Periyodik heartbeat task'ı.

        WatchdogSec aktif değilse hızlı dön — boş döngüde sleep yok.
        """
        if not self._enabled or not _watchdog_active():
            return
        while True:
            self._tick()
            try:
                await asyncio.sleep(self._interval)
            except asyncio.CancelledError:
                break
