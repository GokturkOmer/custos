"""ThresholdWatcher birim testleri (DB gerektirmez — CI'da koşar).

Critical loop alarm üretiminin (review H1) çekirdek davranışı mock DB + mock
reading_source ile doğrulanır: breach→debounce→alarm, hysteresis clear, emergency
auto-clear bypass, bakım modunda is_test, bakım-sonrası yeniden değerlendirme
(H5) ve per-threshold hata izolasyonu (C1).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

import custos.critical.threshold_watcher as tw
from custos.critical.threshold_watcher import ThresholdWatcher
from custos.shared.database import AlarmEvent, Threshold


@pytest.fixture(autouse=True)
def _no_maintenance(monkeypatch: pytest.MonkeyPatch) -> None:
    """Varsayılan: bakım modu kapalı (testler gerektikçe override eder)."""
    monkeypatch.setattr(tw, "is_global_maintenance", AsyncMock(return_value=False))
    monkeypatch.setattr(tw, "is_instance_in_maintenance", AsyncMock(return_value=False))


def _threshold(**kw: object) -> Threshold:
    base: dict[str, object] = {
        "tag_id": "T1", "name": "Test", "direction": "high",
        "set_point": 80.0, "debounce_seconds": 0, "severity": "warn", "id": 1,
    }
    base.update(kw)
    return Threshold(**base)  # type: ignore[arg-type]


def _mock_db(
    thresholds: list[Threshold],
    active: AlarmEvent | None = None,
) -> AsyncMock:
    db = AsyncMock()
    db.list_thresholds.return_value = thresholds
    db.list_tag_bindings_all.return_value = []
    db.get_active_alarm_for_threshold.return_value = active
    db.insert_alarm_event.return_value = AlarmEvent(tag_id="T1", id=99)
    return db


def _src(tag_id: str, value: float) -> dict[str, tw.TagReading]:
    return {tag_id: tw.TagReading(timestamp=datetime.now(UTC), tag_id=tag_id, value=value)}


async def _run(watcher: ThresholdWatcher, cycles: int = 2) -> None:
    await watcher._refresh_definitions()
    for _ in range(cycles):
        await watcher._evaluate_cycle()


async def test_breach_triggers_alarm_after_debounce() -> None:
    """High breach (debounce 0) → ikinci cycle'da source='threshold' alarm yazılır."""
    db = _mock_db([_threshold()])
    readings = _src("T1", 90.0)
    await _run(ThresholdWatcher(db, lambda: readings))

    db.insert_alarm_event.assert_awaited_once()
    event = db.insert_alarm_event.await_args.args[0]
    assert event.source == "threshold"
    assert event.is_test is False
    assert event.trigger_value == 90.0
    assert event.severity == "warn"
    assert event.message  # kendi-kendine yeten açıklama


async def test_no_breach_writes_nothing() -> None:
    """Eşik altı değer → alarm yazılmaz."""
    db = _mock_db([_threshold()])
    readings = _src("T1", 50.0)
    await _run(ThresholdWatcher(db, lambda: readings))
    db.insert_alarm_event.assert_not_awaited()


async def test_clears_with_hysteresis() -> None:
    """Aktif alarm + değer ölü bandın altında → cleared update."""
    active = AlarmEvent(tag_id="T1", id=10, state="triggered", threshold_id=1)
    db = _mock_db([_threshold(hysteresis=5.0)], active=active)
    readings = _src("T1", 70.0)  # 80-5=75 altı → temizlenir
    await _run(ThresholdWatcher(db, lambda: readings), cycles=1)

    db.update_alarm_event.assert_awaited_once()
    args = db.update_alarm_event.await_args.args
    assert args[0] == 10
    assert args[1]["state"] == "cleared"


async def test_emergency_no_auto_clear() -> None:
    """Emergency: değer normale dönse bile auto-clear OLMAZ."""
    active = AlarmEvent(tag_id="T1", id=11, state="triggered", threshold_id=1)
    db = _mock_db([_threshold(severity="emergency", hysteresis=5.0)], active=active)
    readings = _src("T1", 10.0)
    await _run(ThresholdWatcher(db, lambda: readings), cycles=1)
    db.update_alarm_event.assert_not_awaited()


async def test_is_test_during_global_maintenance(monkeypatch: pytest.MonkeyPatch) -> None:
    """Global bakım açıkken breach → alarm is_test=True."""
    monkeypatch.setattr(tw, "is_global_maintenance", AsyncMock(return_value=True))
    db = _mock_db([_threshold()])
    readings = _src("T1", 90.0)
    await _run(ThresholdWatcher(db, lambda: readings))

    db.insert_alarm_event.assert_awaited_once()
    assert db.insert_alarm_event.await_args.args[0].is_test is True


async def test_h5_clears_test_alarm_after_maintenance_ends() -> None:
    """H5: aktif alarm is_test ama artık bakım yok + breach sürüyor →
    test alarmı kapatılır + debounce sıfırlanır (gerçek alarm yeniden doğacak)."""
    active = AlarmEvent(tag_id="T1", id=12, state="triggered", is_test=True, threshold_id=1)
    db = _mock_db([_threshold()], active=active)
    # is_global / is_instance default False (bakım bitti)
    watcher = ThresholdWatcher(db, lambda: _src("T1", 90.0))
    watcher._debounce_tracker[1] = datetime.now(UTC)  # önceden debounce vardı
    await watcher._refresh_definitions()
    await watcher._evaluate_cycle()

    db.update_alarm_event.assert_awaited_once()
    assert db.update_alarm_event.await_args.args[1]["state"] == "cleared"
    assert 1 not in watcher._debounce_tracker  # debounce sıfırlandı


async def test_h5_noop_when_still_in_maintenance(monkeypatch: pytest.MonkeyPatch) -> None:
    """is_test alarm + HÂLÂ bakımda + breach → no-op (test alarmı kapanmaz)."""
    monkeypatch.setattr(tw, "is_global_maintenance", AsyncMock(return_value=True))
    active = AlarmEvent(tag_id="T1", id=13, state="triggered", is_test=True, threshold_id=1)
    db = _mock_db([_threshold()], active=active)
    await _run(ThresholdWatcher(db, lambda: _src("T1", 90.0)), cycles=1)
    db.update_alarm_event.assert_not_awaited()


async def test_per_threshold_isolation() -> None:
    """Bir threshold'un değerlendirmesi patlarsa diğeri yine işlenir (C1)."""
    t_bad = _threshold(tag_id="BAD", id=1)
    t_good = _threshold(tag_id="GOOD", id=2)
    db = _mock_db([t_bad, t_good])

    async def _active(threshold_id: int) -> AlarmEvent | None:
        if threshold_id == 1:
            raise RuntimeError("bozuk threshold")
        return None

    db.get_active_alarm_for_threshold.side_effect = _active
    readings = {
        "BAD": tw.TagReading(timestamp=datetime.now(UTC), tag_id="BAD", value=90.0),
        "GOOD": tw.TagReading(timestamp=datetime.now(UTC), tag_id="GOOD", value=90.0),
    }
    watcher = ThresholdWatcher(db, lambda: readings)
    watcher._debounce_tracker[2] = datetime.now(UTC) - timedelta(seconds=5)
    await watcher._refresh_definitions()
    await watcher._evaluate_cycle()  # BAD patlar ama GOOD işlenmeli

    # GOOD için alarm yazıldı (BAD'in hatası cycle'ı düşürmedi)
    assert db.insert_alarm_event.await_count == 1
    assert db.insert_alarm_event.await_args.args[0].tag_id == "GOOD"
