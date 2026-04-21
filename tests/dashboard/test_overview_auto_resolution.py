"""F11 Paket D — Overview/detail handler auto-resolution + gather testleri.

İki şeyi doğrular:
- Handler'lar `query_readings_auto` çağırıyor (eski downsampled/raw değil).
- Tüm chart × tag sorguları `asyncio.gather` ile paralel çalışıyor (çağrılar
  bir gate'i birlikte beklerse paralel; seri olsalardı timeout'a takılırdı).
- Template context'inde `resolution` bilgisi pencereye göre doğru rozet üretiyor.
"""

from __future__ import annotations

import asyncio
from collections.abc import Generator
from datetime import datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from custos.__main__ import app
from custos.analytics.dashboard.app import _resolution_hint_for
from custos.shared.database import (
    OverviewChart,
    OverviewChartTag,
    TagReading,
    TagRecord,
)


def _mk_tag(tag_id: str, unit: str = "C") -> TagRecord:
    """Test için minimal TagRecord."""
    return TagRecord(
        tag_id=tag_id,
        name=tag_id,
        modbus_host="127.0.0.1",
        register_address=0,
        unit=unit,
    )


def _mk_chart(chart_key: str, minutes: int, sort_order: int = 0) -> OverviewChart:
    """Test için minimal OverviewChart."""
    return OverviewChart(
        chart_key=chart_key,
        title=chart_key.upper(),
        sort_order=sort_order,
        time_window_minutes=minutes,
    )


def _mk_reading(tag_id: str, ts: datetime, value: float) -> TagReading:
    return TagReading(timestamp=ts, tag_id=tag_id, value=value, quality_flag=0)


class _GatedMockDB:
    """Mock DB — query_readings_auto çağrıları bir gate'te birlikte bekler.

    Beklenen N çağrı toplandığında gate açılır ve tümü dönmeye başlar. Seri
    çalışan bir handler'da ilk çağrı gate'i beklemekle bloke olur ve timeout
    yaşanır — bu da gather'ın gerçekten paralel olduğunu kanıtlar.
    """

    def __init__(
        self,
        charts: list[OverviewChart],
        chart_tag_bindings: dict[str, list[str]],
        tags: list[TagRecord],
        expected_query_count: int,
    ) -> None:
        self._charts = charts
        self._chart_tag_bindings = chart_tag_bindings
        self._tags = tags
        self.expected_query_count = expected_query_count
        self.gate: asyncio.Event | None = None
        self.auto_calls: list[dict[str, Any]] = []
        self.downsampled_calls: list[dict[str, Any]] = []
        self.raw_calls: list[dict[str, Any]] = []

    # --- Overview için lazım olan metotlar ---

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
        if chart_key is None:
            out: list[OverviewChartTag] = []
            for ck, tids in self._chart_tag_bindings.items():
                out.extend(
                    OverviewChartTag(chart_key=ck, tag_id=tid) for tid in tids
                )
            return out
        return [
            OverviewChartTag(chart_key=chart_key, tag_id=tid)
            for tid in self._chart_tag_bindings.get(chart_key, [])
        ]

    async def list_upcoming_maintenance_tasks(
        self, within_hours: int = 48,
    ) -> list[Any]:
        return []

    async def get_threshold(self, threshold_id: int) -> Any:
        return None

    async def get_asset_instance(self, instance_id: int) -> Any:
        return None

    # --- Auto-resolution dispatch test'leri için ölçülen metotlar ---

    async def query_readings_auto(
        self,
        tag_id: str,
        start: datetime,
        end: datetime,
        target_points: int = 600,
    ) -> list[TagReading]:
        self.auto_calls.append({
            "tag_id": tag_id, "start": start, "end": end,
            "target_points": target_points,
        })
        if self.gate is not None:
            # Beklenen sayıya ulaşınca aç — paralel çalışıyorsa hepsi geçer.
            if len(self.auto_calls) >= self.expected_query_count:
                self.gate.set()
            await asyncio.wait_for(self.gate.wait(), timeout=3.0)
        return [_mk_reading(tag_id, start, 1.0)]

    async def query_tag_readings_downsampled(
        self,
        tag_id: str,
        start: datetime,
        end: datetime,
        target_points: int = 600,
    ) -> list[TagReading]:
        self.downsampled_calls.append({"tag_id": tag_id})
        return []

    async def query_tag_readings(
        self, tag_id: str, start: datetime, end: datetime,
    ) -> list[TagReading]:
        self.raw_calls.append({"tag_id": tag_id})
        return []

    # --- Detail için ek metotlar ---

    async def get_overview_chart(
        self, chart_key: str,
    ) -> OverviewChart | None:
        for c in self._charts:
            if c.chart_key == chart_key:
                return c
        return None


def _install_db(db: _GatedMockDB) -> Any:
    """app.state.db'i kurarken eski referansı saklayan helper."""
    prev = getattr(app.state, "db", None)
    app.state.db = db
    return prev


def _restore_db(prev: Any) -> None:
    if prev is None:
        if hasattr(app.state, "db"):
            delattr(app.state, "db")
    else:
        app.state.db = prev


@pytest.fixture
def mock_db_overview() -> Generator[_GatedMockDB, None, None]:
    """3 chart × 2 tag = 6 paralel sorgu için gate'li mock DB."""
    charts = [
        _mk_chart("c1", minutes=15, sort_order=0),
        _mk_chart("c2", minutes=360, sort_order=1),
        _mk_chart("c3", minutes=1440, sort_order=2),
    ]
    bindings = {
        "c1": ["t1", "t2"],
        "c2": ["t1", "t2"],
        "c3": ["t1", "t2"],
    }
    tags = [_mk_tag("t1"), _mk_tag("t2")]
    db = _GatedMockDB(
        charts=charts,
        chart_tag_bindings=bindings,
        tags=tags,
        expected_query_count=6,
    )
    db.gate = asyncio.Event()
    prev = _install_db(db)
    yield db
    _restore_db(prev)


def test_overview_uses_query_readings_auto(
    mock_db_overview: _GatedMockDB,
) -> None:
    """Overview handler eski downsampled yerine query_readings_auto kullanır."""
    client = TestClient(app)
    response = client.get("/dashboard/overview")
    assert response.status_code == 200
    assert len(mock_db_overview.auto_calls) == 6
    assert mock_db_overview.downsampled_calls == []


def test_overview_chart_parallel_execution(
    mock_db_overview: _GatedMockDB,
) -> None:
    """gather → 6 sorgu paralel başlar; gate timeout yoksa seri değil.

    Mock gate'i N çağrı toplandığında açar; seri çalışan handler'da ilk
    çağrı bloke kalır ve 3s timeout fırlatılır (request 500 dönerdi).
    """
    client = TestClient(app)
    response = client.get("/dashboard/overview")
    assert response.status_code == 200
    assert len(mock_db_overview.auto_calls) == 6
    # Her tag için her chart'ta 1 çağrı = 6 çağrı; tag_id seti {t1,t2}
    seen_tags = {c["tag_id"] for c in mock_db_overview.auto_calls}
    assert seen_tags == {"t1", "t2"}


@pytest.mark.parametrize(
    ("minutes", "expected_hint"),
    [
        (15, "ham"),         # ≤ 1 saat
        (360, "dakika"),     # 6 saat (≤ 1 gün)
        (1440, "dakika"),    # tam 24 saat — inclusive eşik 'dakika'
    ],
)
def test_resolution_hint_in_response_context(
    minutes: int, expected_hint: str,
) -> None:
    """Pencere büyüklüğüne göre rozet HTML'de görünür (parametrize)."""
    charts = [_mk_chart("solo", minutes=minutes)]
    db = _GatedMockDB(
        charts=charts,
        chart_tag_bindings={"solo": ["t1"]},
        tags=[_mk_tag("t1")],
        expected_query_count=1,
    )
    db.gate = asyncio.Event()
    prev = _install_db(db)
    try:
        client = TestClient(app)
        response = client.get("/dashboard/overview")
        assert response.status_code == 200
        # chart_panel macro'sundaki data-resolution-hint attribute aranır
        assert f'data-resolution-hint="{expected_hint}"' in response.text
    finally:
        _restore_db(prev)


def test_resolution_hint_helper_is_pure() -> None:
    """_resolution_hint_for saf fonksiyon — eşikleri sabit (>1 gün → 'saat')."""
    assert _resolution_hint_for(timedelta(minutes=30)) == "ham"
    assert _resolution_hint_for(timedelta(hours=1)) == "ham"  # inclusive
    assert _resolution_hint_for(timedelta(hours=6)) == "dakika"
    assert _resolution_hint_for(timedelta(hours=24)) == "dakika"  # inclusive
    assert _resolution_hint_for(timedelta(days=2)) == "saat"


def test_detail_page_uses_auto_for_large_window() -> None:
    """Detail handler query_tag_readings yerine query_readings_auto kullanır."""
    chart = _mk_chart("solo", minutes=360)  # 6 saat → "dakika" katmanı
    db = _GatedMockDB(
        charts=[chart],
        chart_tag_bindings={"solo": ["t1", "t2"]},
        tags=[_mk_tag("t1"), _mk_tag("t2")],
        expected_query_count=2,
    )
    db.gate = asyncio.Event()
    prev = _install_db(db)
    try:
        client = TestClient(app)
        response = client.get("/dashboard/overview/charts/solo/view")
        assert response.status_code == 200
        # Detay sayfası ham query_tag_readings çağırmamalı
        assert len(db.raw_calls) == 0
        # query_readings_auto her tag için 1 kez çağrılmalı
        assert len(db.auto_calls) == 2
        assert {c["tag_id"] for c in db.auto_calls} == {"t1", "t2"}
        # Resolution rozeti detay sayfasında da görünür
        assert 'data-resolution-hint="dakika"' in response.text
    finally:
        _restore_db(prev)


def test_overview_with_no_charts_returns_200() -> None:
    """Boş chart listesi senaryosunda 200 döner — regresyon koruması."""
    db = _GatedMockDB(
        charts=[],
        chart_tag_bindings={},
        tags=[],
        expected_query_count=0,
    )
    db.gate = None  # gate gerekmez, çağrı yok
    prev = _install_db(db)
    try:
        client = TestClient(app)
        response = client.get("/dashboard/overview")
        assert response.status_code == 200
        assert len(db.auto_calls) == 0
    finally:
        _restore_db(prev)
