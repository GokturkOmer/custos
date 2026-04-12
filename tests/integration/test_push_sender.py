"""Push sender entegrasyon testleri.

Sessiz saat ve severity filtresi kontrolü.
"""

from __future__ import annotations

from datetime import time

from custos.analytics.push_sender import _is_quiet_hour, _should_notify
from custos.shared.database import PushSubscription


def _make_sub(
    notify_warn: bool = True,
    notify_crit: bool = True,
    quiet_start: time | None = None,
    quiet_end: time | None = None,
) -> PushSubscription:
    """Test için PushSubscription oluşturur."""
    return PushSubscription(
        endpoint="https://test.push/sender",
        p256dh="test-p256dh",
        auth="test-auth",
        notify_warn=notify_warn,
        notify_crit=notify_crit,
        quiet_start=quiet_start,
        quiet_end=quiet_end,
    )


def test_quiet_hour_normal_range() -> None:
    """Normal aralık (gece yarısını geçmeyen) doğru çalışmalı."""
    sub = _make_sub(quiet_start=time(8, 0), quiet_end=time(18, 0))

    assert _is_quiet_hour(sub, time(10, 0)) is True
    assert _is_quiet_hour(sub, time(8, 0)) is True
    assert _is_quiet_hour(sub, time(18, 0)) is True
    assert _is_quiet_hour(sub, time(7, 59)) is False
    assert _is_quiet_hour(sub, time(18, 1)) is False


def test_quiet_hour_overnight_range() -> None:
    """Gece yarısını geçen aralık (ör: 22:00-07:00) doğru çalışmalı."""
    sub = _make_sub(quiet_start=time(22, 0), quiet_end=time(7, 0))

    assert _is_quiet_hour(sub, time(23, 0)) is True
    assert _is_quiet_hour(sub, time(0, 0)) is True
    assert _is_quiet_hour(sub, time(6, 59)) is True
    assert _is_quiet_hour(sub, time(7, 0)) is True
    assert _is_quiet_hour(sub, time(21, 59)) is False
    assert _is_quiet_hour(sub, time(7, 1)) is False


def test_quiet_hour_none() -> None:
    """Sessiz saat tanımlı değilse her zaman False döndürmeli."""
    sub = _make_sub(quiet_start=None, quiet_end=None)
    assert _is_quiet_hour(sub, time(12, 0)) is False


def test_should_notify_filters_by_severity() -> None:
    """Severity filtresi doğru çalışmalı."""
    # warn kapalı, crit açık
    sub = _make_sub(notify_warn=False, notify_crit=True)
    assert _should_notify(sub, "warn", time(12, 0)) is False
    assert _should_notify(sub, "crit", time(12, 0)) is True

    # warn açık, crit kapalı
    sub2 = _make_sub(notify_warn=True, notify_crit=False)
    assert _should_notify(sub2, "warn", time(12, 0)) is True
    assert _should_notify(sub2, "crit", time(12, 0)) is False


def test_should_notify_respects_quiet_hours() -> None:
    """Sessiz saatlerde bildirim gitmemeli."""
    sub = _make_sub(
        notify_warn=True,
        notify_crit=True,
        quiet_start=time(22, 0),
        quiet_end=time(7, 0),
    )
    # Sessiz saatte
    assert _should_notify(sub, "crit", time(23, 0)) is False
    # Normal saatte
    assert _should_notify(sub, "crit", time(12, 0)) is True


def test_should_notify_all_enabled() -> None:
    """Tüm filtreler açıksa bildirim gitmeli."""
    sub = _make_sub()
    assert _should_notify(sub, "warn", time(12, 0)) is True
    assert _should_notify(sub, "crit", time(12, 0)) is True
