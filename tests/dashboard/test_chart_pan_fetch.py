"""v1.1 R-03 — chart pan-fetch JSON endpoint testleri.

Dogrular:
- `GET /overview/charts/{chart_key}/readings?start=&end=` 200 + JSON sema
- Gecersiz aralik (end<=start, gelecek, 10y eski, partial) → 400
- chart_key bulunamadi → 404
- 0 tag bagli chart → bos series
- chart_data payload `pan_fetch_url` + `min_allowed_ts` icerir (3 yil cap)
"""

from __future__ import annotations

import json
import re
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from custos.__main__ import app
from custos.analytics.dashboard.app import _LONG_WINDOW_DAYS
from custos.shared.database import (
    OverviewChart,
    OverviewChartTag,
    TagReading,
    TagRecord,
)
from custos.shared.query_guard import QueryGuardError


def _mk_tag(tag_id: str, unit: str = "C") -> TagRecord:
    return TagRecord(
        tag_id=tag_id,
        name=tag_id,
        modbus_host="127.0.0.1",
        register_address=0,
        unit=unit,
    )


def _mk_chart(chart_key: str, minutes: int = 60) -> OverviewChart:
    return OverviewChart(
        chart_key=chart_key,
        title=chart_key.upper(),
        sort_order=0,
        time_window_minutes=minutes,
    )


def _mk_reading(tag_id: str, ts: datetime, value: float) -> TagReading:
    return TagReading(timestamp=ts, tag_id=tag_id, value=value, quality_flag=0)


class _PanFetchMockDB:
    """Mock DB — pan-fetch endpoint cagrilarini kaydeder, opsiyonel guard error firlatir."""

    def __init__(
        self,
        charts: list[OverviewChart],
        bindings: dict[str, list[str]],
        tags: list[TagRecord],
        *,
        guard_raise: bool = False,
    ) -> None:
        self._charts = charts
        self._bindings = bindings
        self._tags = tags
        self._guard_raise = guard_raise
        self.auto_calls: list[dict[str, Any]] = []

    async def list_alarm_events(
        self,
        state: str | None = None,
        limit: int = 100,
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
        self,
        chart_key: str | None = None,
    ) -> list[OverviewChartTag]:
        if chart_key is None:
            return [
                OverviewChartTag(chart_key=ck, tag_id=tid)
                for ck, tids in self._bindings.items()
                for tid in tids
            ]
        return [
            OverviewChartTag(chart_key=chart_key, tag_id=tid)
            for tid in self._bindings.get(chart_key, [])
        ]

    async def list_upcoming_maintenance_tasks(
        self,
        within_hours: int = 48,
    ) -> list[Any]:
        return []

    async def get_threshold(self, threshold_id: int) -> Any:
        return None

    async def get_asset_instance(self, instance_id: int) -> Any:
        return None

    async def get_overview_chart(self, chart_key: str) -> OverviewChart | None:
        for c in self._charts:
            if c.chart_key == chart_key:
                return c
        return None

    async def query_readings_auto(
        self,
        tag_id: str,
        start: datetime,
        end: datetime,
        target_points: int = 600,
        tag_count: int = 1,
    ) -> list[TagReading]:
        self.auto_calls.append(
            {
                "tag_id": tag_id,
                "start": start,
                "end": end,
                "target_points": target_points,
                "tag_count": tag_count,
            }
        )
        if self._guard_raise:
            raise QueryGuardError("Pencere fazla genis")
        return [
            _mk_reading(tag_id, start, 1.0),
            _mk_reading(tag_id, start + (end - start) / 2, 2.0),
        ]


def _install_db(db: _PanFetchMockDB) -> Any:
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
def pan_db() -> Generator[_PanFetchMockDB, None, None]:
    db = _PanFetchMockDB(
        charts=[_mk_chart("solo")],
        bindings={"solo": ["t1", "t2"]},
        tags=[_mk_tag("t1"), _mk_tag("t2", unit="bar")],
    )
    prev = _install_db(db)
    yield db
    _restore_db(prev)


@pytest.fixture
def pan_db_empty() -> Generator[_PanFetchMockDB, None, None]:
    """0 tag'li chart — pan-fetch bos series donmeli."""
    db = _PanFetchMockDB(
        charts=[_mk_chart("empty")],
        bindings={"empty": []},
        tags=[],
    )
    prev = _install_db(db)
    yield db
    _restore_db(prev)


@pytest.fixture
def pan_db_guard() -> Generator[_PanFetchMockDB, None, None]:
    """Query guard reject simulasyonu."""
    db = _PanFetchMockDB(
        charts=[_mk_chart("solo")],
        bindings={"solo": ["t1"]},
        tags=[_mk_tag("t1")],
        guard_raise=True,
    )
    prev = _install_db(db)
    yield db
    _restore_db(prev)


def test_readings_endpoint_returns_json_schema(pan_db: _PanFetchMockDB) -> None:
    """Dogru aralik 200 + json sema (timestamps + series + labels + resolution + window)."""
    now = datetime.now(UTC)
    end_ts = int(now.timestamp()) - 60
    start_ts = end_ts - 1800  # 30 dk
    client = TestClient(app)
    response = client.get(
        f"/dashboard/overview/charts/solo/readings?start={start_ts}&end={end_ts}",
    )
    assert response.status_code == 200
    data = response.json()
    assert set(data.keys()) >= {
        "timestamps",
        "series",
        "labels",
        "resolution",
        "window_start",
        "window_end",
    }
    # 2 tag bagli, label sirasi binding'le ayni
    assert data["labels"] == ["t1 (C)", "t2 (bar)"]
    assert len(data["series"]) == 2
    assert data["resolution"] == "ham"  # 30 dk → 1 sa esigi alti
    assert data["window_start"] == start_ts
    assert data["window_end"] == end_ts
    # Mock 2 reading dondurdu
    assert len(data["timestamps"]) == 2
    # query_readings_auto her iki tag icin de cagrildi
    assert len(pan_db.auto_calls) == 2
    assert {c["tag_id"] for c in pan_db.auto_calls} == {"t1", "t2"}
    # tag_count=2 (toplu yuk degerlendirmesi icin)
    assert all(c["tag_count"] == 2 for c in pan_db.auto_calls)


def test_readings_endpoint_invalid_end_before_start(pan_db: _PanFetchMockDB) -> None:
    """end <= start → 400."""
    now_ts = int(datetime.now(UTC).timestamp())
    client = TestClient(app)
    response = client.get(
        f"/dashboard/overview/charts/solo/readings?start={now_ts}&end={now_ts - 100}",
    )
    assert response.status_code == 400
    assert "baslangictan" in response.text.lower()
    assert pan_db.auto_calls == []


def test_readings_endpoint_future_end_rejected(pan_db: _PanFetchMockDB) -> None:
    """end gelecek tarihte → 400."""
    future = int(datetime.now(UTC).timestamp()) + 86400
    start = future - 3600
    client = TestClient(app)
    response = client.get(
        f"/dashboard/overview/charts/solo/readings?start={start}&end={future}",
    )
    assert response.status_code == 400
    assert "gelecekte" in response.text.lower()
    assert pan_db.auto_calls == []


def test_readings_endpoint_too_old_start_rejected(pan_db: _PanFetchMockDB) -> None:
    """start 10 yildan eski → 400 (3650 gun guard)."""
    too_old = int((datetime.now(UTC) - timedelta(days=3700)).timestamp())
    end = too_old + 3600
    client = TestClient(app)
    response = client.get(
        f"/dashboard/overview/charts/solo/readings?start={too_old}&end={end}",
    )
    assert response.status_code == 400
    assert "10 yil" in response.text.lower() or "10 yildan" in response.text.lower()
    assert pan_db.auto_calls == []


def test_readings_endpoint_non_numeric_start_rejected(pan_db: _PanFetchMockDB) -> None:
    """Sayisal olmayan epoch → 400."""
    client = TestClient(app)
    response = client.get(
        "/dashboard/overview/charts/solo/readings?start=abc&end=2026",
    )
    assert response.status_code == 400
    assert pan_db.auto_calls == []


def test_readings_endpoint_chart_not_found(pan_db: _PanFetchMockDB) -> None:
    """chart_key yok → 404."""
    now_ts = int(datetime.now(UTC).timestamp())
    client = TestClient(app)
    response = client.get(
        f"/dashboard/overview/charts/yok/readings?start={now_ts - 3600}&end={now_ts - 60}",
    )
    assert response.status_code == 404
    assert pan_db.auto_calls == []


def test_readings_endpoint_empty_chart_returns_empty_arrays(
    pan_db_empty: _PanFetchMockDB,
) -> None:
    """0 tag'li chart → timestamps=[], series=[], labels=[]."""
    now_ts = int(datetime.now(UTC).timestamp())
    client = TestClient(app)
    response = client.get(
        f"/dashboard/overview/charts/empty/readings?start={now_ts - 3600}&end={now_ts - 60}",
    )
    assert response.status_code == 200
    data = response.json()
    assert data["timestamps"] == []
    assert data["series"] == []
    assert data["labels"] == []
    # Hic query yapilmamis olmali
    assert pan_db_empty.auto_calls == []


def test_readings_endpoint_query_guard_error_returns_400(
    pan_db_guard: _PanFetchMockDB,
) -> None:
    """QueryGuardError → 400 + TR mesaj."""
    now_ts = int(datetime.now(UTC).timestamp())
    client = TestClient(app)
    response = client.get(
        f"/dashboard/overview/charts/solo/readings?start={now_ts - 86400}&end={now_ts - 60}",
    )
    assert response.status_code == 400
    assert "genis" in response.text.lower() or "guard" in response.text.lower()


def test_readings_endpoint_resolution_changes_with_window(
    pan_db: _PanFetchMockDB,
) -> None:
    """Pencere genisleyince resolution badge degisir (ham → dakika → saat)."""
    now_ts = int(datetime.now(UTC).timestamp())
    client = TestClient(app)

    # 30 dk → ham
    r1 = client.get(
        f"/dashboard/overview/charts/solo/readings?start={now_ts - 1800}&end={now_ts - 60}",
    )
    assert r1.json()["resolution"] == "ham"

    # 12 saat → dakika
    r2 = client.get(
        f"/dashboard/overview/charts/solo/readings"
        f"?start={now_ts - 12 * 3600}&end={now_ts - 60}",
    )
    assert r2.json()["resolution"] == "dakika"

    # 30 gun → saat
    r3 = client.get(
        f"/dashboard/overview/charts/solo/readings"
        f"?start={now_ts - 30 * 86400}&end={now_ts - 60}",
    )
    assert r3.json()["resolution"] == "saat"


def test_chart_detail_payload_includes_pan_fetch_fields(
    pan_db: _PanFetchMockDB,
) -> None:
    """Detail sayfasi chart_data payload'a pan_fetch_url + min_allowed_ts ekler."""
    client = TestClient(app)
    response = client.get("/dashboard/overview/charts/solo/view")
    assert response.status_code == 200
    # window.custos.chartData = { "solo": {...} }
    match = re.search(
        r'window\.custos\.chartData\s*=\s*\{\s*"solo":\s*(\{.*?\})\s*\}',
        response.text,
        re.DOTALL,
    )
    assert match is not None, "chart_data payload bulunamadi"
    payload = json.loads(match.group(1))
    assert payload["pan_fetch_url"] == "/dashboard/overview/charts/solo/readings"
    assert isinstance(payload["min_allowed_ts"], int)
    # min_allowed_ts ~ now - 3 yil (epoch saniye)
    expected_lower = int(
        (datetime.now(UTC) - timedelta(days=_LONG_WINDOW_DAYS + 1)).timestamp()
    )
    expected_upper = int(
        (datetime.now(UTC) - timedelta(days=_LONG_WINDOW_DAYS - 1)).timestamp()
    )
    assert expected_lower < payload["min_allowed_ts"] < expected_upper
    # Template'de data-pan-fetch="true" attribute'u render edilmis
    assert 'data-pan-fetch="true"' in response.text
