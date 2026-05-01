"""Push sender entegrasyon testleri.

Sessiz saat ve severity filtresi kontrolü. P-03 ile genişletildi:
``enabled`` toggle, ``notify_info``/``notify_emergency`` ayrı kolonlar,
master switch.

v1.0.1 borç #3: ``_build_payload`` ``alarm_id`` parametresiyle
notification tag'i unique yapar; ardışık alarmlar bildirim merkezinde
ayrı satır olarak birikir (önceki davranış: aynı severity tek satır
olarak ezilir).
"""

from __future__ import annotations

import json
from datetime import time

import pytest

from custos.analytics.push_sender import (
    _build_payload,
    _is_quiet_hour,
    _should_notify,
)
from custos.shared.database import PushSubscription


def _make_sub(
    notify_warn: bool = True,
    notify_crit: bool = True,
    notify_info: bool = False,
    notify_emergency: bool = True,
    enabled: bool = True,
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
        notify_info=notify_info,
        notify_emergency=notify_emergency,
        enabled=enabled,
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


def test_should_notify_emergency_bypasses_quiet_hours_only() -> None:
    """P-03: emergency sessiz saat bypass eder ama notify_emergency'ye bağlı.

    Önceki davranış (P-02): emergency tüm filtreleri bypass.
    Yeni davranış: notify_emergency=False ise emergency de gitmez —
    kullanıcı kontrolü.
    """
    # notify_emergency açık + sessiz saatte → gönderilir (bypass)
    sub_em_on = _make_sub(
        notify_warn=False,
        notify_crit=False,
        notify_emergency=True,
        quiet_start=time(0, 0),
        quiet_end=time(23, 59),
    )
    assert _should_notify(sub_em_on, "emergency", time(3, 0)) is True
    assert _should_notify(sub_em_on, "emergency", time(12, 0)) is True

    # notify_emergency kapalı → gönderilmez
    sub_em_off = _make_sub(notify_emergency=False)
    assert _should_notify(sub_em_off, "emergency", time(12, 0)) is False


def test_should_notify_info_uses_separate_column() -> None:
    """P-03: info severity'i artık ayrı ``notify_info`` kolonunu kullanır."""
    # notify_info=True → info gider (notify_warn'dan bağımsız)
    sub_info_on = _make_sub(notify_info=True, notify_warn=False)
    assert _should_notify(sub_info_on, "info", time(12, 0)) is True

    # notify_info=False (default) → info gitmez (notify_warn fallback YOK artık)
    sub_info_off = _make_sub(notify_info=False, notify_warn=True)
    assert _should_notify(sub_info_off, "info", time(12, 0)) is False


def test_should_notify_disabled_subscription_skips_all_tiers() -> None:
    """P-03: ``enabled=False`` cihaz hiçbir tier almaz, emergency dahil."""
    sub = _make_sub(
        enabled=False,
        notify_info=True,
        notify_warn=True,
        notify_crit=True,
        notify_emergency=True,
    )
    assert _should_notify(sub, "info", time(12, 0)) is False
    assert _should_notify(sub, "warn", time(12, 0)) is False
    assert _should_notify(sub, "crit", time(12, 0)) is False
    assert _should_notify(sub, "emergency", time(12, 0)) is False


def test_should_notify_unknown_severity_defaults_off() -> None:
    """Bilinmeyen severity (defansif): gönderme."""
    sub = _make_sub()
    assert _should_notify(sub, "unknown", time(12, 0)) is False


def test_should_notify_respects_quiet_hours() -> None:
    """Sessiz saatlerde bildirim gitmemeli (emergency dışında)."""
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
    sub = _make_sub(notify_info=True)
    assert _should_notify(sub, "info", time(12, 0)) is True
    assert _should_notify(sub, "warn", time(12, 0)) is True
    assert _should_notify(sub, "crit", time(12, 0)) is True
    assert _should_notify(sub, "emergency", time(12, 0)) is True


def test_build_payload_default_tag_uses_severity() -> None:
    """``alarm_id=None`` (default) → tag custos-{severity} (geri uyumlu).

    Disk/resource/escalation gibi alarm-id'siz çağrılarda eski davranış
    korunmalı.
    """
    payload = json.loads(_build_payload("Title", "Body", "warn"))
    assert payload["tag"] == "custos-warn"
    assert payload["title"] == "Title"
    assert payload["priority"] == "normal"


def test_build_payload_alarm_id_makes_tag_unique() -> None:
    """``alarm_id`` verilince tag custos-{id}; ardışık alarmlar ayrı satır."""
    payload_1 = json.loads(
        _build_payload("Title", "Body", "warn", alarm_id=42),
    )
    payload_2 = json.loads(
        _build_payload("Title", "Body", "warn", alarm_id=43),
    )
    assert payload_1["tag"] == "custos-42"
    assert payload_2["tag"] == "custos-43"
    # Aynı severity ama farklı alarm_id → tag'ler farklı.
    assert payload_1["tag"] != payload_2["tag"]


def test_build_payload_emergency_priority_unaffected_by_alarm_id() -> None:
    """``alarm_id`` priority alanını etkilemez — emergency hâlâ 'high'."""
    with_id = json.loads(
        _build_payload("T", "B", "emergency", alarm_id=1),
    )
    without_id = json.loads(_build_payload("T", "B", "emergency"))
    assert with_id["priority"] == "high"
    assert without_id["priority"] == "high"


def test_quiet_hour_timezone_conversion(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sessiz saat hesabı kullanıcının yerel saatinde yapılmalı.

    Senaryo: Kullanıcı Istanbul'da (UTC+3), sessiz saat 22:00-07:00 lokal.
    UTC 20:00 = Istanbul 23:00 → sessiz saatte.
    UTC 10:00 = Istanbul 13:00 → sessiz saatte değil.
    """
    from datetime import UTC, datetime
    from zoneinfo import ZoneInfo

    sub = _make_sub(
        quiet_start=time(22, 0),
        quiet_end=time(7, 0),
    )

    # UTC 20:00 → Istanbul 23:00 → sessiz saatte
    utc_time_in_quiet = datetime(2026, 6, 10, 20, 0, tzinfo=UTC)
    istanbul_tz = ZoneInfo("Europe/Istanbul")
    local_time = utc_time_in_quiet.astimezone(istanbul_tz).time()
    assert _is_quiet_hour(sub, local_time) is True

    # UTC 10:00 → Istanbul 13:00 → sessiz saatte değil
    utc_time_outside_quiet = datetime(2026, 6, 10, 10, 0, tzinfo=UTC)
    local_time2 = utc_time_outside_quiet.astimezone(istanbul_tz).time()
    assert _is_quiet_hour(sub, local_time2) is False
