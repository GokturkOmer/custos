"""R-05a: ``_row_to_alarm_event_with_label`` parser birim testi.

LEFT JOIN sonucu satırının ``label_*`` kolonlarını parse eder. ``label_id``
NULL ise ``AlarmEvent.label`` None olur (etiketsiz alarm); dolu ise
``AlarmEventLabel`` örneği bağlanır.

asyncpg.Record gerçek bir kayıt değil — ``__getitem__`` ile alan erişimi
yeterli. Burada ``dict[str, Any]`` ile taklit ediliyor; helper sadece
``row[key]`` çağırdığı için kontrat eşit.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from custos.shared.database import (
    AlarmEvent,
    AlarmEventLabel,
    _row_to_alarm_event_with_label,
)


def _base_alarm_row(**overrides: Any) -> dict[str, Any]:
    """Alarm SELECT'inin tüm alarm_events kolonlarını dolduran satır."""
    triggered = datetime(2026, 4, 28, 12, 0, tzinfo=UTC)
    row: dict[str, Any] = {
        "id": 42,
        "threshold_id": 7,
        "tag_id": "TAG_A",
        "state": "triggered",
        "triggered_at": triggered,
        "acknowledged_at": None,
        "cleared_at": None,
        "trigger_value": 85.5,
        "clear_value": None,
        "notes": "",
        "is_test": False,
        "source": "threshold",
        "severity": "warn",
        "message": "",
        # R-06 / Migration 036 kolonları (default NULL)
        "escalated_from": None,
        "escalated_at": None,
        "created_at": triggered,
        # LEFT JOIN kolonları (varsayılan: etiket yok)
        "label_id": None,
        "label_class": None,
        "labeled_by_user_id": None,
        "labeled_at": None,
        "label_notes": None,
    }
    row.update(overrides)
    return row


def test_row_to_alarm_event_with_label_unlabeled_returns_none_label() -> None:
    """label_id NULL → AlarmEvent.label = None (etiketsiz alarm)."""
    row = _base_alarm_row()

    event = _row_to_alarm_event_with_label(row)

    assert isinstance(event, AlarmEvent)
    assert event.id == 42
    assert event.tag_id == "TAG_A"
    assert event.label is None


def test_row_to_alarm_event_with_label_populates_label_when_present() -> None:
    """label_id dolu → AlarmEventLabel parse edilir, alarm.id ile bağlanır."""
    labeled_at = datetime(2026, 4, 28, 13, 30, tzinfo=UTC)
    row = _base_alarm_row(
        label_id=99,
        label_class="gercek_ariza",
        labeled_by_user_id=3,
        labeled_at=labeled_at,
        label_notes="Vanada arıza tespit edildi",
    )

    event = _row_to_alarm_event_with_label(row)

    assert event.label is not None
    assert isinstance(event.label, AlarmEventLabel)
    assert event.label.id == 99
    assert event.label.alarm_event_id == 42  # alarm.id ile aynı
    assert event.label.label_class == "gercek_ariza"
    assert event.label.labeled_by_user_id == 3
    assert event.label.labeled_at == labeled_at
    assert event.label.notes == "Vanada arıza tespit edildi"


def test_row_to_alarm_event_with_label_preserves_alarm_columns() -> None:
    """Alarm kolonları _row_to_alarm_event ile aynı şekilde dolar."""
    row = _base_alarm_row(
        state="acknowledged",
        clear_value=70.0,
        is_test=True,
        source="liveness",
        severity="crit",
        message="Sensör donuk",
    )

    event = _row_to_alarm_event_with_label(row)

    assert event.state == "acknowledged"
    assert event.clear_value == 70.0
    assert event.is_test is True
    assert event.source == "liveness"
    assert event.severity == "crit"
    assert event.message == "Sensör donuk"
