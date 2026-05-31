"""R-06 / V11-304-305: threshold_engine'in saf yardımcı fonksiyonlarına
unit test'ler — DB'siz, tick yok.

- ``_cross_sensor_holds`` 6 operator × 2 sonuç (12 case) + bilinmeyen
  operator için defansif True dönüşü.
- ``_RATE_OF_CHANGE_COOLDOWN`` ve ``_CROSS_SENSOR_COOLDOWN`` doğru
  timedelta'ları (paket dokümantasyonu — rate 5 dk, cross 10 dk).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from custos.analytics.threshold_engine import (
    _CROSS_SENSOR_COOLDOWN,
    _RATE_OF_CHANGE_COOLDOWN,
    ThresholdEngine,
    _cross_sensor_holds,
)
from custos.shared.database import AlarmEvent, AuditLogEntry


def test_cross_sensor_holds_lt_true_when_a_less_than_b() -> None:
    """lt: a < b → True (kural sağlanıyor, ihlal yok)."""
    assert _cross_sensor_holds(5.0, "lt", 10.0) is True


def test_cross_sensor_holds_lt_false_when_a_equal_or_greater() -> None:
    """lt: a >= b → False (ihlal var)."""
    assert _cross_sensor_holds(10.0, "lt", 10.0) is False
    assert _cross_sensor_holds(15.0, "lt", 10.0) is False


def test_cross_sensor_holds_gt() -> None:
    """gt: a > b → True; aksi → False."""
    assert _cross_sensor_holds(15.0, "gt", 10.0) is True
    assert _cross_sensor_holds(10.0, "gt", 10.0) is False
    assert _cross_sensor_holds(5.0, "gt", 10.0) is False


def test_cross_sensor_holds_eq_neq() -> None:
    """eq / neq — birbirinin tersi kontrolleri."""
    assert _cross_sensor_holds(7.5, "eq", 7.5) is True
    assert _cross_sensor_holds(7.5, "eq", 8.0) is False
    assert _cross_sensor_holds(7.5, "neq", 8.0) is True
    assert _cross_sensor_holds(7.5, "neq", 7.5) is False


def test_cross_sensor_holds_lte_gte() -> None:
    """lte / gte — eşitlikte True."""
    assert _cross_sensor_holds(10.0, "lte", 10.0) is True
    assert _cross_sensor_holds(9.0, "lte", 10.0) is True
    assert _cross_sensor_holds(11.0, "lte", 10.0) is False
    assert _cross_sensor_holds(10.0, "gte", 10.0) is True
    assert _cross_sensor_holds(11.0, "gte", 10.0) is True
    assert _cross_sensor_holds(9.0, "gte", 10.0) is False


def test_cross_sensor_holds_unknown_operator_returns_true_defensively() -> None:
    """Bilinmeyen operator → True (DB CHECK zaten engeller; kötü değer
    gelirse alarm bombardımanı olmasın).
    """
    assert _cross_sensor_holds(5.0, "wat", 10.0) is True


def test_layer1_cooldown_constants() -> None:
    """Paket dokümanı: rate-of-change cooldown 5 dk, cross-sensor 10 dk."""
    assert _RATE_OF_CHANGE_COOLDOWN == timedelta(minutes=5)
    assert _CROSS_SENSOR_COOLDOWN == timedelta(minutes=10)


class _ClearStubDB:
    """``_auto_clear_cross_sensor`` (review H6) için minimal DB yüzeyi."""

    def __init__(self, *, update_returns: AlarmEvent | None) -> None:
        self._update_returns = update_returns
        self.update_calls: list[tuple[int, dict[str, Any]]] = []
        self.audit_calls: list[AuditLogEntry] = []

    async def update_alarm_event(
        self,
        event_id: int,
        updates: dict[str, Any],
    ) -> AlarmEvent | None:
        self.update_calls.append((event_id, dict(updates)))
        return self._update_returns

    async def insert_audit_log(self, entry: AuditLogEntry) -> AuditLogEntry:
        self.audit_calls.append(entry)
        return entry


async def test_auto_clear_cross_sensor_clears_tracked_alarm() -> None:
    """H6: izlenen aktif alarm, kural tekrar sağlanınca 'cleared' yapılır +
    audit yazılır + takip haritasından silinir."""
    db = _ClearStubDB(
        update_returns=AlarmEvent(id=5, tag_id="A", state="cleared"),
    )
    engine = ThresholdEngine(db=db)  # type: ignore[arg-type]
    engine._cross_active_alarm[42] = 5
    now = datetime.now(UTC)

    await engine._auto_clear_cross_sensor(42, 7.5, now)

    assert len(db.update_calls) == 1
    event_id, updates = db.update_calls[0]
    assert event_id == 5
    assert updates["state"] == "cleared"
    assert updates["cleared_at"] == now
    assert updates["clear_value"] == 7.5
    # Takip kaydı temizlendi — koşul tekrar ihlal edilirse yeni alarm açılabilir.
    assert 42 not in engine._cross_active_alarm
    assert len(db.audit_calls) == 1
    assert db.audit_calls[0].action == "cross_sensor_auto_cleared"


async def test_auto_clear_cross_sensor_noop_when_rule_not_tracked() -> None:
    """Takip kaydı yoksa (ör. süreç restart sonrası) hiçbir DB çağrısı yok."""
    db = _ClearStubDB(update_returns=None)
    engine = ThresholdEngine(db=db)  # type: ignore[arg-type]
    now = datetime.now(UTC)

    await engine._auto_clear_cross_sensor(99, 1.0, now)

    assert db.update_calls == []
    assert db.audit_calls == []
