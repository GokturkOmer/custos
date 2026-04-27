"""ResourceMonitor (CPU + RAM) push entegrasyonu testleri (V11-111 / P-06).

DiskMonitor test'i ile ayni desen — ``send_push_notifications`` AsyncMock ile
stub'lanir, ``psutil`` sample'i monkeypatch ile sabitlenir, DB
``get_retention_config`` AsyncMock ile esik dondurur.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from custos.analytics import resource_telemetry
from custos.analytics.resource_telemetry import (
    ALERT_COOLDOWN_SECONDS,
    ResourceMonitor,
    ResourceSample,
)
from custos.shared.database import RetentionConfig


def _make_db_mock(cpu_threshold: int = 90, ram_threshold: int = 90) -> MagicMock:
    """Esikleri sabit dondurecek DB mock'u — get_retention_config tek metot."""
    db = MagicMock()
    db.get_retention_config = AsyncMock(
        return_value=RetentionConfig(
            raw_retention_days=365,
            auto_clean_enabled=True,
            updated_at=datetime.now(UTC),
            updated_by="test",
            resource_cpu_warn_pct=cpu_threshold,
            resource_ram_warn_pct=ram_threshold,
        ),
    )
    return db


def _patch_sample(
    monkeypatch: pytest.MonkeyPatch,
    cpu_pct: float,
    ram_pct: float,
) -> None:
    """psutil yerine deterministik sample dondurur."""
    monkeypatch.setattr(
        resource_telemetry,
        "get_resource_sample",
        lambda: ResourceSample(
            timestamp=datetime.now(UTC),
            cpu_percent=cpu_pct,
            ram_percent=ram_pct,
        ),
    )


@pytest.mark.asyncio
async def test_buffer_not_full_no_push(monkeypatch: pytest.MonkeyPatch) -> None:
    """Buffer 5 sample'a dolmadan eşik aşilsa bile push gönderilmemeli."""
    _patch_sample(monkeypatch, cpu_pct=95.0, ram_pct=95.0)
    push_mock = AsyncMock(return_value=0)
    monkeypatch.setattr(resource_telemetry, "send_push_notifications", push_mock)

    monitor = ResourceMonitor(db=_make_db_mock())
    # 4 tick — buffer 4'e dolar, henüz mean tetiklenmez
    for _ in range(4):
        await monitor.run_once()
    push_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_cpu_threshold_exceeded_sends_warn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """5 sample mean %95 → eşik %90 üstü, CPU push (severity=warn) tetiklenir."""
    _patch_sample(monkeypatch, cpu_pct=95.0, ram_pct=50.0)
    push_mock = AsyncMock(return_value=1)
    monkeypatch.setattr(resource_telemetry, "send_push_notifications", push_mock)

    monitor = ResourceMonitor(db=_make_db_mock())
    for _ in range(5):
        await monitor.run_once()

    assert push_mock.await_count == 1, "5 dk dolu eşik üstü → CPU push 1 kez"
    call_kwargs = push_mock.await_args.kwargs
    assert call_kwargs["severity"] == "warn"
    assert "CPU yuksek" in call_kwargs["title"]
    # Body 5 dk ortalaması ve eşik içermeli
    assert "%95" in call_kwargs["body"]


@pytest.mark.asyncio
async def test_cpu_cooldown_within_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """CPU alarmı sonrası cooldown içinde ikinci tick sessiz olmalı."""
    _patch_sample(monkeypatch, cpu_pct=95.0, ram_pct=50.0)
    push_mock = AsyncMock(return_value=1)
    monkeypatch.setattr(resource_telemetry, "send_push_notifications", push_mock)

    monitor = ResourceMonitor(db=_make_db_mock())
    for _ in range(5):
        await monitor.run_once()
    assert push_mock.await_count == 1

    # 6. tick — cooldown içinde, yeni push beklenmez
    await monitor.run_once()
    assert push_mock.await_count == 1, "Cooldown içinde 2. push olmamali"


@pytest.mark.asyncio
async def test_cpu_and_ram_independent_alerts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CPU + RAM ayrı cooldown — biri tetiklendi diye diğeri susmaz."""
    _patch_sample(monkeypatch, cpu_pct=95.0, ram_pct=95.0)
    push_mock = AsyncMock(return_value=1)
    monkeypatch.setattr(resource_telemetry, "send_push_notifications", push_mock)

    monitor = ResourceMonitor(db=_make_db_mock())
    for _ in range(5):
        await monitor.run_once()
    # CPU + RAM iki ayrı push — ikisi de eşik üstü, ayrı cooldown'lar
    assert push_mock.await_count == 2
    titles = {call.kwargs["title"] for call in push_mock.await_args_list}
    assert titles == {"CPU yuksek", "RAM yuksek"}


@pytest.mark.asyncio
async def test_cooldown_expired_sends_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cooldown süresi geçtiyse ikinci uyarı gönderilebilmeli."""
    _patch_sample(monkeypatch, cpu_pct=95.0, ram_pct=50.0)
    push_mock = AsyncMock(return_value=1)
    monkeypatch.setattr(resource_telemetry, "send_push_notifications", push_mock)

    monitor = ResourceMonitor(db=_make_db_mock())
    for _ in range(5):
        await monitor.run_once()
    assert push_mock.await_count == 1

    # Cooldown'ı geçmişe taşı — süre geçmiş varsayılır
    monitor._last_cpu_alert_at = datetime.now(UTC) - timedelta(
        seconds=ALERT_COOLDOWN_SECONDS + 1,
    )
    await monitor.run_once()
    assert push_mock.await_count == 2
