"""Sensors live-values endpoint testleri."""

from __future__ import annotations

from fastapi.testclient import TestClient

from custos.__main__ import app

client = TestClient(app)


def test_live_values_endpoint_returns_503_or_200() -> None:
    """Live values endpoint'i DB yoksa 503, varsa 200 döndürmeli."""
    response = client.get("/dashboard/sensors/live-values")
    assert response.status_code in (200, 503)


def test_sensors_page_contains_htmx_attribute() -> None:
    """Sensors sayfasında HTMX live-values polling attribute'ü olmalı."""
    response = client.get("/dashboard/sensors")
    if response.status_code == 200:
        assert "hx-get" in response.text
        assert "/dashboard/sensors/live-values" in response.text
