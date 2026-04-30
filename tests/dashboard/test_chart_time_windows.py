"""v1.1 R-02 — uzun pencere preset'leri ve custom date range testleri.

Doğrular:
- `_ALLOWED_TIME_WINDOWS` 11 değer içerir (15 dk - 1 yıl)
- `_format_time_window_label` Türkçe + dakika/saat/gün/hafta/ay/yıl etiketleri
- Detail handler `?start=&end=` parametreleriyle özel aralık çağırır
- Geçersiz aralık (end<=start, gelecek tarih, 10 yıldan eski) 400 döner
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from custos.__main__ import app
from custos.analytics.dashboard.app import (
    _ALLOWED_TIME_WINDOWS,
    _format_time_window_label,
)
from custos.shared.config import settings
from custos.shared.database import (
    OverviewChart,
    OverviewChartTag,
    TagReading,
    TagRecord,
)


def test_allowed_time_windows_count() -> None:
    """v1.1 R-02 sonrası 11 pencere preset'i bulunmalı (7 mevcut + 4 yeni)."""
    assert len(_ALLOWED_TIME_WINDOWS) == 11
    assert _ALLOWED_TIME_WINDOWS == frozenset(
        {15, 30, 60, 180, 360, 720, 1440, 10080, 43200, 129600, 525600}
    )


@pytest.mark.parametrize(
    ("minutes", "expected"),
    [
        (15, "Son 15 dk"),
        (30, "Son 30 dk"),
        (60, "Son 1 sa"),
        (180, "Son 3 sa"),
        (720, "Son 12 sa"),
        (1440, "Son 1 gun"),  # 24 saat = 1 gün
        (10080, "Son 1 hafta"),  # 7 gün
        (43200, "Son 1 ay"),  # 30 gün
        (129600, "Son 3 ay"),  # 90 gün
        (525600, "Son 1 yil"),  # 365 gün
    ],
)
def test_format_time_window_label_thresholds(minutes: int, expected: str) -> None:
    """Etiket fonksiyonu yeni 4 eşik dahil tüm preset'lere doğru cevap verir."""
    assert _format_time_window_label(minutes) == expected


def _mk_tag(tag_id: str, unit: str = "C") -> TagRecord:
    return TagRecord(
        tag_id=tag_id,
        name=tag_id,
        modbus_host="127.0.0.1",
        register_address=0,
        unit=unit,
    )


def _mk_chart(chart_key: str, minutes: int) -> OverviewChart:
    return OverviewChart(
        chart_key=chart_key,
        title=chart_key.upper(),
        sort_order=0,
        time_window_minutes=minutes,
    )


def _mk_reading(tag_id: str, ts: datetime, value: float) -> TagReading:
    return TagReading(timestamp=ts, tag_id=tag_id, value=value, quality_flag=0)


class _CustomRangeMockDB:
    """Mock DB — query_readings_auto'ya geçen start/end argümanlarını kaydeder."""

    def __init__(
        self,
        charts: list[OverviewChart],
        bindings: dict[str, list[str]],
        tags: list[TagRecord],
    ) -> None:
        self._charts = charts
        self._bindings = bindings
        self._tags = tags
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
        return [_mk_reading(tag_id, start, 1.0)]


def _install_db(db: _CustomRangeMockDB) -> Any:
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
def detail_db() -> Generator[_CustomRangeMockDB, None, None]:
    db = _CustomRangeMockDB(
        charts=[_mk_chart("solo", minutes=60)],
        bindings={"solo": ["t1"]},
        tags=[_mk_tag("t1")],
    )
    prev = _install_db(db)
    yield db
    _restore_db(prev)


def test_custom_range_passed_to_query(detail_db: _CustomRangeMockDB) -> None:
    """`?start=&end=` query string ile çağrı, query_readings_auto'ya o aralık iletilir."""
    local_tz = ZoneInfo(settings.custos_timezone)
    # Yerel saat 5 gün önce - 4 gün önce → UTC'ye düşüldükten sonra dakika katmanı (>1 gün → saat)
    now_local = datetime.now(local_tz)
    start_local = (now_local - timedelta(days=5)).replace(microsecond=0)
    end_local = (now_local - timedelta(days=4)).replace(microsecond=0)
    start_str = start_local.strftime("%Y-%m-%dT%H:%M")
    end_str = end_local.strftime("%Y-%m-%dT%H:%M")

    client = TestClient(app)
    response = client.get(
        f"/dashboard/overview/charts/solo/view?start={start_str}&end={end_str}"
    )
    assert response.status_code == 200
    assert len(detail_db.auto_calls) == 1
    call = detail_db.auto_calls[0]
    # Mock'a geçen start/end UTC olmalı (handler astimezone çağırır)
    assert call["start"].tzinfo == UTC
    assert call["end"].tzinfo == UTC
    # Pencere yaklaşık 1 gün — saatlik resolution
    delta = call["end"] - call["start"]
    assert timedelta(hours=23) < delta < timedelta(hours=25)
    # 1 gün > 1 gün eşiği değil (inclusive); 1 günden uzun olduğunda 'saat'
    # Tam 1 gün = dakika; biz 24 saatin altında olabiliriz, "dakika" da olabilir
    # önemli olan custom range form'unun render edilmesi
    assert 'data-test-id="custom-range-form"' in response.text
    assert 'data-custom-range="true"' in response.text


def test_custom_range_invalid_end_before_start(detail_db: _CustomRangeMockDB) -> None:
    """end <= start ise sayfa default pencere + uyari banner ile acilir (S4c)."""
    client = TestClient(app)
    response = client.get(
        "/dashboard/overview/charts/solo/view"
        "?start=2026-04-20T10:00&end=2026-04-20T08:00",
    )
    # S4c (commit 698b397): tarih hatasinda 400 yerine 200 + banner doner.
    # Sayfa default zaman penceresi ile acilir, kullanici hatayi banner'da gorur.
    assert response.status_code == 200
    assert "ozel tarih araligi uygulanamadi" in response.text.lower()
    assert "baslangictan" in response.text.lower()
    # Default pencereyle DB sorgu yapildi (eski davranista 400 sebebiyle hic
    # cagrilmiyordu).
    assert len(detail_db.auto_calls) == 1


def test_custom_range_invalid_future_end(detail_db: _CustomRangeMockDB) -> None:
    """end gelecek tarihte ise 200 + uyari banner."""
    local_tz = ZoneInfo(settings.custos_timezone)
    future = datetime.now(local_tz) + timedelta(days=2)
    start_str = (future - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M")
    end_str = future.strftime("%Y-%m-%dT%H:%M")
    client = TestClient(app)
    response = client.get(
        f"/dashboard/overview/charts/solo/view?start={start_str}&end={end_str}",
    )
    assert response.status_code == 200
    assert "ozel tarih araligi uygulanamadi" in response.text.lower()
    assert "gelecekte" in response.text.lower()
    assert len(detail_db.auto_calls) == 1


def test_custom_range_too_old_start(detail_db: _CustomRangeMockDB) -> None:
    """start 10 yildan eski ise 200 + uyari banner."""
    local_tz = ZoneInfo(settings.custos_timezone)
    too_old = datetime.now(local_tz) - timedelta(days=3700)
    end_local = too_old + timedelta(hours=1)
    client = TestClient(app)
    response = client.get(
        "/dashboard/overview/charts/solo/view"
        f"?start={too_old.strftime('%Y-%m-%dT%H:%M')}"
        f"&end={end_local.strftime('%Y-%m-%dT%H:%M')}",
    )
    assert response.status_code == 200
    assert "ozel tarih araligi uygulanamadi" in response.text.lower()
    assert "10 yil" in response.text.lower() or "10 yildan" in response.text.lower()
    assert len(detail_db.auto_calls) == 1


def test_custom_range_partial_params_rejected(detail_db: _CustomRangeMockDB) -> None:
    """Sadece start ya da sadece end verilirse 200 + uyari banner."""
    client = TestClient(app)
    response = client.get(
        "/dashboard/overview/charts/solo/view?start=2026-04-20T08:00",
    )
    assert response.status_code == 200
    assert "ozel tarih araligi uygulanamadi" in response.text.lower()
    assert len(detail_db.auto_calls) == 1


def test_no_custom_range_uses_time_window(detail_db: _CustomRangeMockDB) -> None:
    """Query string yok → mevcut time_window_minutes davranışı korunur."""
    client = TestClient(app)
    response = client.get("/dashboard/overview/charts/solo/view")
    assert response.status_code == 200
    assert len(detail_db.auto_calls) == 1
    call = detail_db.auto_calls[0]
    # 60 dk pencere — aralık yaklaşık 1 saat
    delta = call["end"] - call["start"]
    assert timedelta(minutes=59) < delta < timedelta(minutes=61)
    # Custom range badge görünmemeli
    assert 'data-custom-range="true"' not in response.text


def test_custom_range_long_range_uses_hourly_resolution(
    detail_db: _CustomRangeMockDB,
) -> None:
    """30 günlük özel aralık → resolution badge 'saat' olmalı."""
    local_tz = ZoneInfo(settings.custos_timezone)
    now_local = datetime.now(local_tz)
    start_local = (now_local - timedelta(days=30)).replace(microsecond=0)
    end_local = (now_local - timedelta(minutes=5)).replace(microsecond=0)
    client = TestClient(app)
    response = client.get(
        "/dashboard/overview/charts/solo/view"
        f"?start={start_local.strftime('%Y-%m-%dT%H:%M')}"
        f"&end={end_local.strftime('%Y-%m-%dT%H:%M')}",
    )
    assert response.status_code == 200
    assert 'data-resolution-hint="saat"' in response.text
