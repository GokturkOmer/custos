"""F11 Paket H — Query guard testleri.

Dört ayrı katmanda doğrulama yapılır:

1. Pure fonksiyon (`evaluate_query`) — dört matris örneği:
   5×1×raw allow / 200×1×raw force-1min / 200×7×1min force-1hour /
   1×15yıl×1hour reject.
2. `query_readings_auto` entegrasyonu — forced_aggregate → katman override;
   reject → `QueryGuardError`. Dispatch, private helper'ları monkeypatch spy
   ile izleyerek ölçülür.
3. Dashboard handler — guard reject'i HTTP 400 + detail metni olarak çevirir.

Pure testler DB'ye bağlanmaz; entegrasyon testi TimescaleDB gerektirir
(`_check_db_available` fixture'ı).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Generator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from custos.__main__ import app
from custos.shared.database import (
    OverviewChart,
    OverviewChartTag,
    TagReading,
    TagRecord,
    TimescaleDBDatabase,
)
from custos.shared.query_guard import (
    QueryGuardError,
    evaluate_query,
)

# --- Pure fonksiyon testleri (DB gerekmiyor) ---


def test_small_query_allowed_raw() -> None:
    """5 tag × 1 gün × raw → 5 ≤ 7 → allow (override yok)."""
    decision = evaluate_query(
        tag_count=5, time_range_days=1.0, requested_layer="raw",
    )
    assert decision.allowed is True
    assert decision.forced_aggregate is None
    assert decision.reason == "OK"


def test_large_raw_forced_to_1min() -> None:
    """200 tag × 1 gün × raw → 200 > 7 → forced 1min."""
    decision = evaluate_query(
        tag_count=200, time_range_days=1.0, requested_layer="raw",
    )
    assert decision.allowed is True
    assert decision.forced_aggregate == "1min"
    assert "raw" in decision.reason.lower() or "dakikalık" in decision.reason


def test_very_large_1min_forced_to_1hour() -> None:
    """200 tag × 7 gün × 1min → 1400 > 200 → forced 1hour."""
    decision = evaluate_query(
        tag_count=200, time_range_days=7.0, requested_layer="1min",
    )
    assert decision.allowed is True
    assert decision.forced_aggregate == "1hour"
    assert "saatlik" in decision.reason


def test_extreme_query_rejected() -> None:
    """1 tag × 15 yıl × 1hour → 5475 > 3650 → reject."""
    decision = evaluate_query(
        tag_count=1,
        time_range_days=15 * 365,
        requested_layer="1hour",
    )
    assert decision.allowed is False
    assert decision.forced_aggregate is None
    assert "parçalı" in decision.reason or "uzun" in decision.reason


# --- Entegrasyon: query_readings_auto dispatch + reject ---

_HelperFn = Callable[..., Awaitable[list[TagReading]]]


def _install_dispatch_spy(
    db: TimescaleDBDatabase, monkeypatch: pytest.MonkeyPatch,
) -> list[str]:
    """Üç private helper'ı sar, hangisinin çağrıldığını kaydet.

    Helper'lar hâlâ gerçek sorgu çalıştırır — tag yoksa boş liste döner.
    """
    calls: list[str] = []
    orig_raw: _HelperFn = db._query_raw_downsampled
    orig_1min: _HelperFn = db._query_1min_downsampled
    orig_1hour: _HelperFn = db._query_1hour_downsampled

    async def spy_raw(*args: object, **kwargs: object) -> list[TagReading]:
        calls.append("raw")
        return await orig_raw(*args, **kwargs)

    async def spy_1min(*args: object, **kwargs: object) -> list[TagReading]:
        calls.append("1min")
        return await orig_1min(*args, **kwargs)

    async def spy_1hour(*args: object, **kwargs: object) -> list[TagReading]:
        calls.append("1hour")
        return await orig_1hour(*args, **kwargs)

    monkeypatch.setattr(db, "_query_raw_downsampled", spy_raw)
    monkeypatch.setattr(db, "_query_1min_downsampled", spy_1min)
    monkeypatch.setattr(db, "_query_1hour_downsampled", spy_1hour)
    return calls


@pytest.mark.usefixtures("_check_db_available")
async def test_query_readings_auto_applies_guard(
    db: TimescaleDBDatabase, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Guard iki yönü de etkiler: forced_aggregate override + reject exception.

    200 tag × 2 gün → pencere 2g → başlangıç 1hour (window > 1 gün); guard
    200×2=400 > 200 kuralı "1min" için tetiklenir, ama biz zaten 1hour'dayız.
    Bu yüzden ham → forced yolunu ayrıca test ederiz:

        Senaryo A: 200 tag × 0.5 saat → başlangıç raw; 200×0.021=4.17 ≤ 7,
        yani forced yok. Daha sert bir örnek lazım.

    Daha net: 200 tag × 1 gün → pencere 1 gün → başlangıç 1min (inclusive);
    guard 200×1=200 ≤ 200 → allowed (eşit eşiktir). Bir dakika ekleyip 1g+60s
    yapalım → window > 1g → başlangıç 1hour, 1hour guard'ı 1 ≤ 3650 → allow.

    Sonuç: mevcut eşiklerle forced durumunu tetiklemek için ya tag_count'u
    artırırız ya da custom Settings. Basit olsun: 500 tag × 30 dakika → raw
    başlar; 500×0.0208=10.42 > 7 → forced 1min → `_query_1min_downsampled`
    çağrılır.
    """
    calls = _install_dispatch_spy(db, monkeypatch)
    end = datetime.now(UTC)
    start = end - timedelta(minutes=30)  # raw katmanı (window ≤ 1h)

    # tag_count=500 × 0.0208 gün = 10.42 > 7 → forced 1min
    await db.query_readings_auto(
        "TEST_NONEXISTENT_GUARD", start, end, tag_count=500,
    )

    # Guard override → 1min helper'ı çağrıldı, raw çağrılmadı
    assert calls == ["1min"]

    # Reject yolu: 1 tag × 15 yıl → 1hour + 5475 > 3650 → QueryGuardError
    long_end = datetime.now(UTC)
    long_start = long_end - timedelta(days=15 * 365)
    with pytest.raises(QueryGuardError) as excinfo:
        await db.query_readings_auto(
            "TEST_NONEXISTENT_GUARD", long_start, long_end, tag_count=1,
        )
    assert "parçalı" in str(excinfo.value) or "uzun" in str(excinfo.value)


# --- Dashboard: reject → HTTP 400 ---


def _mk_chart(chart_key: str, minutes: int) -> OverviewChart:
    return OverviewChart(
        chart_key=chart_key,
        title=chart_key.upper(),
        sort_order=0,
        time_window_minutes=minutes,
    )


def _mk_tag(tag_id: str) -> TagRecord:
    return TagRecord(
        tag_id=tag_id, name=tag_id,
        modbus_host="127.0.0.1", register_address=0,
    )


class _RejectingMockDB:
    """Dashboard'un QueryGuardError'ı 400'e çevirdiğini doğrulamak için mock.

    `query_readings_auto` her çağrıldığında guard reject simüle eder.
    Diğer metotlar overview/detail handler'larının çalışabilmesi için
    minimum stubs döner (boş listeler + var olan chart).
    """

    def __init__(self, charts: list[OverviewChart], tags: list[TagRecord]) -> None:
        self._charts = charts
        self._tags = tags

    async def list_alarm_events(
        self, state: str | None = None, limit: int = 100,
    ) -> list[Any]:
        return []

    async def list_tags(self, status: str | None = None) -> list[TagRecord]:
        return self._tags

    async def list_asset_instances(self) -> list[Any]:
        return []

    async def count_anomalies(self, since: datetime) -> int:
        return 0

    async def list_overview_charts(self) -> list[OverviewChart]:
        return list(self._charts)

    async def list_overview_chart_tags(
        self, chart_key: str | None = None,
    ) -> list[OverviewChartTag]:
        if not self._charts:
            return []
        ck = chart_key or self._charts[0].chart_key
        return [OverviewChartTag(chart_key=ck, tag_id=self._tags[0].tag_id)]

    async def list_upcoming_maintenance_tasks(
        self, within_hours: int = 48,
    ) -> list[Any]:
        return []

    async def get_threshold(self, threshold_id: int) -> Any:
        return None

    async def get_asset_instance(self, instance_id: int) -> Any:
        return None

    async def get_overview_chart(
        self, chart_key: str,
    ) -> OverviewChart | None:
        for c in self._charts:
            if c.chart_key == chart_key:
                return c
        return None

    async def query_readings_auto(
        self, tag_id: str, start: datetime, end: datetime,
        target_points: int = 600, tag_count: int = 1,
    ) -> list[TagReading]:
        msg = "Çok uzun pencere (5475 gün > 3650 gün) — parçalı sorgu yapın."
        raise QueryGuardError(msg)


@pytest.fixture
def reject_db() -> Generator[_RejectingMockDB, None, None]:
    charts = [_mk_chart("solo", minutes=60)]
    tags = [_mk_tag("t1")]
    db = _RejectingMockDB(charts=charts, tags=tags)
    prev = getattr(app.state, "db", None)
    app.state.db = db
    try:
        yield db
    finally:
        if prev is None:
            if hasattr(app.state, "db"):
                delattr(app.state, "db")
        else:
            app.state.db = prev


def test_dashboard_returns_400_on_rejected_query(
    reject_db: _RejectingMockDB,
) -> None:
    """Guard reject → dashboard overview handler'ı HTTP 400 + detail metni döner."""
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/dashboard/overview")
    assert response.status_code == 400
    detail = response.json().get("detail", "")
    assert "parçalı" in detail or "uzun" in detail
