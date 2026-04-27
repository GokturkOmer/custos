"""Stuck-at preset çözümleme unit testleri (V11-108, P-05).

LivenessEngine ``query_tag_readings`` öncesi ``resolve_stuck_at_seconds``
ve ``_check_stuck_at`` / ``_check_counter`` saf-fonksiyonlarını test
eder; DB veya I/O yok.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from custos.analytics.liveness_engine import _check_counter, _check_stuck_at
from custos.shared.database import TagReading, TagRecord
from custos.shared.stuck_at_presets import (
    PRESET_SECONDS,
    resolve_effective_preset,
    resolve_stuck_at_seconds,
)


def _make_tag(
    *,
    unit: str = "",
    preset: str = "auto",
    seconds: int | None = None,
) -> TagRecord:
    """Test için minimal TagRecord builder."""
    return TagRecord(
        tag_id="TEST",
        name="Test",
        modbus_host="127.0.0.1",
        register_address=0,
        unit=unit,
        stuck_at_preset=preset,
        stuck_at_seconds=seconds,
    )


def _make_reading(value: float, ts: datetime) -> TagReading:
    """Test için TagReading builder."""
    return TagReading(timestamp=ts, tag_id="TEST", value=value, quality_flag="ok")


# --- resolve_stuck_at_seconds (4 test) ---


def test_resolve_stuck_at_seconds_auto_temperature() -> None:
    """Sıcaklık (°C) otomatik 'slow' preset → 1800 sn."""
    tag = _make_tag(unit="°C", preset="auto")
    assert resolve_effective_preset(tag) == "slow"
    assert resolve_stuck_at_seconds(tag) == PRESET_SECONDS["slow"] == 1800


def test_resolve_stuck_at_seconds_auto_pressure() -> None:
    """Basınç (bar) otomatik 'fast' preset → 300 sn."""
    tag = _make_tag(unit="bar", preset="auto")
    assert resolve_effective_preset(tag) == "fast"
    assert resolve_stuck_at_seconds(tag) == 300


def test_resolve_stuck_at_seconds_manual_override() -> None:
    """Manuel saniye override preset'in saniyesini geçersiz kılar."""
    tag = _make_tag(unit="°C", preset="slow", seconds=600)
    # Preset hâlâ 'slow' — counter mantığına geçmek için preset adı korunur
    assert resolve_effective_preset(tag) == "slow"
    # Ama saniye override edildi
    assert resolve_stuck_at_seconds(tag) == 600


def test_resolve_stuck_at_seconds_none_disabled() -> None:
    """Preset 'none' → kontrol kapalı, override edilse bile None döner."""
    tag = _make_tag(unit="°C", preset="none")
    assert resolve_stuck_at_seconds(tag) is None

    # Override + preset='none' birlikteyse: 'none' her zaman kontrolü kapatır
    tag_with_override = _make_tag(unit="°C", preset="none", seconds=600)
    assert resolve_stuck_at_seconds(tag_with_override) is None


# --- _check_stuck_at (2 test) ---


def test_check_stuck_at_triggers_after_threshold_exceeded() -> None:
    """Son değer 600 sn'dir değişmiyor + eşik 300 sn → alarm mesajı."""
    now = datetime.now(UTC)
    readings = [
        _make_reading(50.0, now - timedelta(seconds=900)),
        _make_reading(50.0, now - timedelta(seconds=600)),
        _make_reading(50.0, now - timedelta(seconds=300)),
        _make_reading(50.0, now),
    ]
    # Tüm değerler 50.0 → en eski (900s önce) son değişim sayılır
    msg = _check_stuck_at(readings, seconds=300, now=now)
    assert msg is not None
    assert "donuk" in msg.lower() or "Sensör" in msg


def test_check_stuck_at_no_alarm_within_threshold() -> None:
    """Son değer 100 sn önce değişti, eşik 300 sn → alarm yok."""
    now = datetime.now(UTC)
    readings = [
        _make_reading(50.0, now - timedelta(seconds=400)),
        _make_reading(51.0, now - timedelta(seconds=100)),  # son değişim
        _make_reading(51.0, now),
    ]
    msg = _check_stuck_at(readings, seconds=300, now=now)
    assert msg is None


# --- _check_counter (2 test) ---


def test_check_counter_decreasing_value_alarm() -> None:
    """Counter geri gitti (1000 → 990) → alarm."""
    now = datetime.now(UTC)
    readings = [
        _make_reading(1000.0, now - timedelta(seconds=600)),
        _make_reading(995.0, now - timedelta(seconds=300)),
        _make_reading(990.0, now),
    ]
    msg = _check_counter(readings, seconds=300)
    assert msg is not None
    assert "geri gitti" in msg


def test_check_counter_stagnant_alarm() -> None:
    """Counter pencere boyunca artmadı + süre eşiği aştı → alarm."""
    now = datetime.now(UTC)
    readings = [
        _make_reading(500.0, now - timedelta(seconds=600)),
        _make_reading(500.0, now - timedelta(seconds=300)),
        _make_reading(500.0, now),
    ]
    # 600 sn pencere, eşik 300 sn → alarm
    msg = _check_counter(readings, seconds=300)
    assert msg is not None
    assert "durağan" in msg or "artmıyor" in msg

    # Eşik 700 sn → henüz alarm yok
    msg_no_alarm = _check_counter(readings, seconds=700)
    assert msg_no_alarm is None
