"""Alarm, Threshold ve Logs dashboard route testleri."""

from __future__ import annotations

from fastapi.testclient import TestClient

from custos.__main__ import app

client = TestClient(app)


def test_alarms_page_returns_503_or_200() -> None:
    """Alarms sayfası DB yoksa 503, varsa 200 döndürmeli."""
    response = client.get("/dashboard/alarms")
    assert response.status_code in (200, 503)


def test_alarms_acknowledge_nonexistent_returns_error() -> None:
    """Var olmayan alarm acknowledge 404 veya 503 döndürmeli."""
    response = client.post(
        "/dashboard/alarms/999999/acknowledge",
        follow_redirects=False,
    )
    assert response.status_code in (404, 503)


def test_alarms_clear_nonexistent_returns_error() -> None:
    """Var olmayan alarm manuel clear 404 veya 503 döndürmeli (review H6)."""
    response = client.post(
        "/dashboard/alarms/999999/clear",
        follow_redirects=False,
    )
    assert response.status_code in (404, 503)


def test_thresholds_page_returns_503_or_200() -> None:
    """Thresholds sayfası DB yoksa 503, varsa 200 döndürmeli."""
    response = client.get("/dashboard/thresholds")
    assert response.status_code in (200, 503)


def test_threshold_delete_nonexistent_returns_error() -> None:
    """Var olmayan threshold silme 404 veya 503 döndürmeli."""
    response = client.post(
        "/dashboard/thresholds/999999/delete",
        follow_redirects=False,
    )
    assert response.status_code in (404, 503)


def test_logs_page_returns_503_or_200() -> None:
    """Logs sayfası DB yoksa 503, varsa 200 döndürmeli."""
    response = client.get("/dashboard/logs")
    assert response.status_code in (200, 503)
