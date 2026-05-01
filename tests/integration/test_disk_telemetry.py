"""Disk telemetrisi ve DiskMonitor push entegrasyonu testleri (F11 Paket F).

- ``get_disk_usage`` stdlib sarmalayıcısı gerçek dizinle çalışır.
- DiskMonitor eşik aşıldığında ``send_push_notifications`` çağırır; cooldown
  içinde ikinci tick sessiz olur.

Testler in-memory; DB ayakta olmadan da çalışır — ``send_push_notifications``
monkeypatch ile stub'lanır.
"""

from __future__ import annotations

import tempfile
from unittest.mock import AsyncMock

import pytest

from custos.analytics import disk_telemetry
from custos.analytics.disk_telemetry import (
    ALERT_COOLDOWN_SECONDS,
    DiskMonitor,
    DiskUsage,
    get_disk_usage,
)


def test_disk_usage_returns_valid_struct() -> None:
    """Gerçek bir dizin için shutil üstünden tutarlı DiskUsage döner."""
    with tempfile.TemporaryDirectory() as tmpdir:
        usage = get_disk_usage(tmpdir)
        assert isinstance(usage, DiskUsage)
        assert usage.mount_point == tmpdir
        assert usage.total_bytes > 0
        assert usage.used_bytes >= 0
        assert usage.free_bytes >= 0
        assert 0.0 <= usage.used_percent <= 100.0
        # used + free toplamı total'dan küçük/eşit (işletim sistemi rezervi ±)
        assert usage.used_bytes + usage.free_bytes <= usage.total_bytes


def test_disk_usage_missing_mount_raises() -> None:
    """Olmayan path FileNotFoundError atmalı — caller log edip geçer."""
    with pytest.raises(FileNotFoundError):
        get_disk_usage("/kesinlikle-olmayan-custos-dizini-xyz")


def test_env_override_changes_default_mount(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``CUSTOS_DISK_MONITOR_PATH`` env'i set edildiğinde default mount o
    path'e geçer; explicit override hâlâ önceliklidir.

    v1.0.1 borç #2: pilot deploy /var/custos mount'unu kurmazsa root FS'e
    düşmesin diye env'den okuma kritik.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setenv("CUSTOS_DISK_MONITOR_PATH", tmpdir)

        # Default (path=None) env'den okur — tmpdir görmeli.
        usage_default = get_disk_usage()
        assert usage_default.mount_point == tmpdir

        # Explicit path verildiğinde env yok sayılır.
        with tempfile.TemporaryDirectory() as other_tmp:
            usage_explicit = get_disk_usage(other_tmp)
            assert usage_explicit.mount_point == other_tmp

        # DiskMonitor de mount_point=None'da env'i çözer.
        monitor = DiskMonitor(db=None)  # type: ignore[arg-type]
        assert monitor._mount_point == tmpdir


def _fake_usage(used_percent: float) -> DiskUsage:
    """Test helper'ı — istediğimiz yüzdede sahte DiskUsage üretir."""
    total = 1000 * 1024**3  # 1000 GB
    used = int(total * used_percent / 100.0)
    return DiskUsage(
        mount_point="/fake",
        total_bytes=total,
        used_bytes=used,
        free_bytes=total - used,
        used_percent=used_percent,
    )


@pytest.mark.asyncio
async def test_high_usage_triggers_push_notification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """%85 üstü ilk tick push gönderir; cooldown içinde ikinci tick sessiz."""
    # get_disk_usage'ı sabit %90 döndürecek stub ile değiştir
    monkeypatch.setattr(
        disk_telemetry,
        "get_disk_usage",
        lambda _path: _fake_usage(90.0),
    )

    # send_push_notifications'ı AsyncMock ile stub'la — gerçek VAPID/DB gerekmez
    push_mock = AsyncMock(return_value=1)
    monkeypatch.setattr(
        disk_telemetry,
        "send_push_notifications",
        push_mock,
    )

    monitor = DiskMonitor(db=None, mount_point="/fake")  # type: ignore[arg-type]
    usage = await monitor.run_once()
    assert usage.used_percent == 90.0
    assert push_mock.await_count == 1, "Eşik aşıldı, push bir kez çağrılmalıydı"
    # Çağrı argümanları — severity warn, body yüzde içermeli
    call_kwargs = push_mock.await_args.kwargs
    assert call_kwargs["severity"] == "warn"
    assert "%90" in call_kwargs["body"]

    # Cooldown içinde ikinci tick — push tekrar çağrılmamalı
    await monitor.run_once()
    assert push_mock.await_count == 1, "Cooldown içinde ikinci push gitmemeli"


@pytest.mark.asyncio
async def test_below_threshold_no_push(monkeypatch: pytest.MonkeyPatch) -> None:
    """%70'te eşik altı; push çağrılmamalı."""
    monkeypatch.setattr(
        disk_telemetry,
        "get_disk_usage",
        lambda _path: _fake_usage(70.0),
    )
    push_mock = AsyncMock(return_value=0)
    monkeypatch.setattr(
        disk_telemetry,
        "send_push_notifications",
        push_mock,
    )

    monitor = DiskMonitor(db=None, mount_point="/fake")  # type: ignore[arg-type]
    await monitor.run_once()
    push_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_cooldown_expired_sends_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cooldown süresi geçtiyse ikinci uyarı gönderilebilmeli."""
    monkeypatch.setattr(
        disk_telemetry,
        "get_disk_usage",
        lambda _path: _fake_usage(95.0),
    )
    push_mock = AsyncMock(return_value=1)
    monkeypatch.setattr(
        disk_telemetry,
        "send_push_notifications",
        push_mock,
    )

    monitor = DiskMonitor(db=None, mount_point="/fake")  # type: ignore[arg-type]
    await monitor.run_once()
    assert push_mock.await_count == 1

    # Cooldown'ı geçmişe taşı — süre geçmiş varsayılacak
    from datetime import UTC, datetime, timedelta

    monitor._last_alert_at = datetime.now(UTC) - timedelta(
        seconds=ALERT_COOLDOWN_SECONDS + 1,
    )
    await monitor.run_once()
    assert push_mock.await_count == 2
