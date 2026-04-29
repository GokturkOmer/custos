"""SystemdWatchdog wrapper birim testleri (V11-105/K13).

Bu testler ``sdnotify`` paketi kurulu olmadan da çalışmalı; `sys.modules`'a
sahte bir modül enjekte ederek "enabled" yolu da kapsanır. systemd dışında
(NOTIFY_SOCKET yok) tüm metodlar no-op davranmalı; bu da ayrıca doğrulanır.
"""

from __future__ import annotations

import asyncio
import sys
import types
from unittest.mock import MagicMock

import pytest

from custos.shared import watchdog
from custos.shared.watchdog import (
    SystemdWatchdog,
    _systemd_active,
    _watchdog_active,
)

# --- Yardımcılar ---


def _inject_fake_sdnotify(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """sys.modules'a sahte sdnotify enjekte eder; SystemdNotifier mock döner.

    Returns:
        Mock notifier — `.notify()` çağrılarını kayıt altına alır.
    """
    notifier = MagicMock()
    fake_module = types.ModuleType("sdnotify")
    fake_module.SystemdNotifier = MagicMock(return_value=notifier)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sdnotify", fake_module)
    return notifier


# --- Env algılama testleri ---


def test_systemd_active_false_when_env_yok(monkeypatch: pytest.MonkeyPatch) -> None:
    """NOTIFY_SOCKET yoksa _systemd_active False döner."""
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    assert _systemd_active() is False


def test_systemd_active_true_when_env_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """NOTIFY_SOCKET set'liyse _systemd_active True döner."""
    monkeypatch.setenv("NOTIFY_SOCKET", "/run/systemd/notify")
    assert _systemd_active() is True


def test_watchdog_active_false_when_env_yok(monkeypatch: pytest.MonkeyPatch) -> None:
    """WATCHDOG_USEC yoksa _watchdog_active False döner."""
    monkeypatch.delenv("WATCHDOG_USEC", raising=False)
    assert _watchdog_active() is False


def test_watchdog_active_true_when_env_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """WATCHDOG_USEC set'liyse _watchdog_active True döner."""
    monkeypatch.setenv("WATCHDOG_USEC", "30000000")
    assert _watchdog_active() is True


# --- systemd dışı (no-op) davranış ---


def test_init_disabled_when_no_notify_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    """NOTIFY_SOCKET yokken Watchdog init no-op (notifier oluşturulmaz)."""
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    wd = SystemdWatchdog()
    assert wd._enabled is False
    assert wd._notifier is None


def test_notify_methods_noop_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disabled durumda notify_ready/stopping/_tick exception fırlatmaz."""
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    wd = SystemdWatchdog()
    # Hiçbir hata atmamalı
    wd.notify_ready()
    wd.notify_stopping()
    wd._tick()


def test_init_disabled_when_sdnotify_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """NOTIFY_SOCKET set ama sdnotify import edilemezse enabled=False."""
    monkeypatch.setenv("NOTIFY_SOCKET", "/run/systemd/notify")
    # sys.modules'tan sil ve ImportError'a zorla
    monkeypatch.setitem(sys.modules, "sdnotify", None)
    wd = SystemdWatchdog()
    assert wd._enabled is False
    assert wd._notifier is None


# --- Enabled yolu (sahte sdnotify ile) ---


def test_init_enabled_with_fake_sdnotify(monkeypatch: pytest.MonkeyPatch) -> None:
    """NOTIFY_SOCKET + sdnotify mevcutsa Watchdog enabled olmalı."""
    monkeypatch.setenv("NOTIFY_SOCKET", "/run/systemd/notify")
    notifier = _inject_fake_sdnotify(monkeypatch)
    wd = SystemdWatchdog()
    assert wd._enabled is True
    assert wd._notifier is notifier


def test_notify_ready_sends_ready_signal(monkeypatch: pytest.MonkeyPatch) -> None:
    """notify_ready, notifier.notify('READY=1') çağırmalı."""
    monkeypatch.setenv("NOTIFY_SOCKET", "/run/systemd/notify")
    notifier = _inject_fake_sdnotify(monkeypatch)
    wd = SystemdWatchdog()
    wd.notify_ready()
    notifier.notify.assert_called_once_with("READY=1")


def test_notify_stopping_sends_stopping_signal(monkeypatch: pytest.MonkeyPatch) -> None:
    """notify_stopping, notifier.notify('STOPPING=1') çağırmalı."""
    monkeypatch.setenv("NOTIFY_SOCKET", "/run/systemd/notify")
    notifier = _inject_fake_sdnotify(monkeypatch)
    wd = SystemdWatchdog()
    wd.notify_stopping()
    notifier.notify.assert_called_once_with("STOPPING=1")


def test_tick_sends_watchdog_signal(monkeypatch: pytest.MonkeyPatch) -> None:
    """_tick, notifier.notify('WATCHDOG=1') çağırmalı."""
    monkeypatch.setenv("NOTIFY_SOCKET", "/run/systemd/notify")
    notifier = _inject_fake_sdnotify(monkeypatch)
    wd = SystemdWatchdog()
    wd._tick()
    notifier.notify.assert_called_once_with("WATCHDOG=1")


# --- Exception yutma ---


def test_notify_ready_swallows_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """notifier.notify exception fırlatırsa notify_ready raise etmemeli."""
    monkeypatch.setenv("NOTIFY_SOCKET", "/run/systemd/notify")
    notifier = _inject_fake_sdnotify(monkeypatch)
    notifier.notify.side_effect = RuntimeError("socket kapalı")
    wd = SystemdWatchdog()
    # Hiçbir exception fırlatmamalı (logger.warning ile loglanır)
    wd.notify_ready()


def test_notify_stopping_swallows_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """notifier.notify exception fırlatırsa notify_stopping raise etmemeli."""
    monkeypatch.setenv("NOTIFY_SOCKET", "/run/systemd/notify")
    notifier = _inject_fake_sdnotify(monkeypatch)
    notifier.notify.side_effect = OSError("ENOENT")
    wd = SystemdWatchdog()
    wd.notify_stopping()


def test_tick_swallows_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """notifier.notify exception fırlatırsa _tick raise etmemeli."""
    monkeypatch.setenv("NOTIFY_SOCKET", "/run/systemd/notify")
    notifier = _inject_fake_sdnotify(monkeypatch)
    notifier.notify.side_effect = ConnectionError("notify socket kopuk")
    wd = SystemdWatchdog()
    wd._tick()


# --- heartbeat_loop ---


@pytest.mark.asyncio
async def test_heartbeat_loop_returns_immediately_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Watchdog disabled ise heartbeat_loop hiç sleep etmeden dönmeli."""
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    monkeypatch.delenv("WATCHDOG_USEC", raising=False)
    wd = SystemdWatchdog()
    # asyncio.wait_for ile süre üst limiti — boş döngü olmadığını ispat eder
    await asyncio.wait_for(wd.heartbeat_loop(), timeout=0.5)


@pytest.mark.asyncio
async def test_heartbeat_loop_returns_immediately_without_watchdog_usec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """sd_notify enabled ama WATCHDOG_USEC yoksa heartbeat'e gerek yok."""
    monkeypatch.setenv("NOTIFY_SOCKET", "/run/systemd/notify")
    monkeypatch.delenv("WATCHDOG_USEC", raising=False)
    _inject_fake_sdnotify(monkeypatch)
    wd = SystemdWatchdog()
    await asyncio.wait_for(wd.heartbeat_loop(), timeout=0.5)


@pytest.mark.asyncio
async def test_heartbeat_loop_ticks_periodically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Aktif Watchdog en az bir tick atar; cancel ile temiz çıkar."""
    monkeypatch.setenv("NOTIFY_SOCKET", "/run/systemd/notify")
    monkeypatch.setenv("WATCHDOG_USEC", "30000000")
    notifier = _inject_fake_sdnotify(monkeypatch)
    wd = SystemdWatchdog(interval_seconds=0.01)

    task = asyncio.create_task(wd.heartbeat_loop())
    # En az 2 tick'e yetecek süre bekle
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        # heartbeat_loop kendi içinde CancelledError yutar, ama asyncio
        # task wrapper'ı tekrar fırlatabilir; her iki durum da kabul.
        pass

    watchdog_calls = [
        call for call in notifier.notify.call_args_list if call.args == ("WATCHDOG=1",)
    ]
    assert len(watchdog_calls) >= 1, "En az 1 WATCHDOG=1 atımı bekleniyor"


@pytest.mark.asyncio
async def test_heartbeat_loop_exits_on_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """heartbeat_loop CancelledError'u yakalayıp düzgün çıkmalı."""
    monkeypatch.setenv("NOTIFY_SOCKET", "/run/systemd/notify")
    monkeypatch.setenv("WATCHDOG_USEC", "30000000")
    _inject_fake_sdnotify(monkeypatch)
    wd = SystemdWatchdog(interval_seconds=10.0)

    task = asyncio.create_task(wd.heartbeat_loop())
    await asyncio.sleep(0.01)
    task.cancel()
    # Loop CancelledError'u yakaladığı için task normal sonlanmalı
    # (ama asyncio task semantics gereği yine de CancelledError alabilir)
    try:
        await asyncio.wait_for(task, timeout=0.5)
    except asyncio.CancelledError:
        pass


# --- Modül erişilebilirliği ---


def test_module_logger_exists() -> None:
    """Modül seviyesindeki logger import edilebilmeli (regression koruması)."""
    assert watchdog.logger is not None
