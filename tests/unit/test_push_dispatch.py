"""push_dispatch.dispatch_once birim testleri (DB gerektirmez — CI'da koşar).

review H1 cutover: Critical threshold alarm'ı yazar (push'suz); Analytics'teki
dispatch loop'u bekleyenleri gönderip ``pushed_at`` ile işaretler.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import custos.analytics.push_dispatch as pd
from custos.analytics.push_dispatch import dispatch_once
from custos.shared.database import AlarmEvent


def _alarm(alarm_id: int, message: str = "msg") -> AlarmEvent:
    return AlarmEvent(
        tag_id="T1", id=alarm_id, severity="warn", source="threshold", message=message,
    )


async def test_dispatch_pushes_and_marks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bekleyen alarm'lar gönderilir + pushed_at ile işaretlenir."""
    send = AsyncMock(return_value=1)
    monkeypatch.setattr(pd, "send_push_notifications", send)
    db = AsyncMock()
    db.list_pending_threshold_push_alarms.return_value = [_alarm(1), _alarm(2)]

    sent = await dispatch_once(db)

    assert sent == 2
    assert send.await_count == 2
    db.mark_alarms_pushed.assert_awaited_once()
    assert db.mark_alarms_pushed.await_args.args[0] == [1, 2]


async def test_dispatch_empty_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bekleyen yoksa push/mark çağrılmaz."""
    send = AsyncMock(return_value=0)
    monkeypatch.setattr(pd, "send_push_notifications", send)
    db = AsyncMock()
    db.list_pending_threshold_push_alarms.return_value = []

    sent = await dispatch_once(db)

    assert sent == 0
    send.assert_not_awaited()
    db.mark_alarms_pushed.assert_not_awaited()


async def test_dispatch_send_failure_not_marked(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bir alarm'ın push'u patlarsa o işaretlenmez (sonraki cycle tekrar denenir),
    diğerleri işlenmeye devam eder."""
    async def _send(**kw: object) -> int:
        if kw.get("alarm_id") == 1:
            raise RuntimeError("push aktarım hatası")
        return 1

    monkeypatch.setattr(pd, "send_push_notifications", AsyncMock(side_effect=_send))
    db = AsyncMock()
    db.list_pending_threshold_push_alarms.return_value = [_alarm(1), _alarm(2)]

    sent = await dispatch_once(db)

    assert sent == 1  # yalnız alarm 2
    db.mark_alarms_pushed.assert_awaited_once()
    assert db.mark_alarms_pushed.await_args.args[0] == [2]


async def test_dispatch_uses_message_as_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """Push gövdesi alarm.message'tan üretilir."""
    send = AsyncMock(return_value=1)
    monkeypatch.setattr(pd, "send_push_notifications", send)
    db = AsyncMock()
    db.list_pending_threshold_push_alarms.return_value = [_alarm(5, message="Yüksek sıcaklık 95")]

    await dispatch_once(db)

    assert send.await_args.kwargs["body"] == "Yüksek sıcaklık 95"
    assert send.await_args.kwargs["alarm_id"] == 5
