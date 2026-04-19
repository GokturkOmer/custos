"""Dashboard route testleri."""

from __future__ import annotations

import os

from fastapi.testclient import TestClient

from custos.__main__ import app

client = TestClient(app)


def test_dashboard_root_redirects() -> None:
    """Dashboard kök URL'i overview'a yönlendirmeli."""
    response = client.get("/dashboard/", follow_redirects=False)
    assert response.status_code == 307
    assert "/dashboard/overview" in response.headers["location"]


def test_overview_returns_200() -> None:
    """Overview sayfası 200 döndürmeli."""
    response = client.get("/dashboard/overview")
    assert response.status_code == 200
    assert "CUSTOS" in response.text


def test_showcase_returns_404_without_dev_mode() -> None:
    """Showcase, dev mode olmadan 404 döndürmeli."""
    os.environ.pop("CUSTOS_DEV_MODE", None)
    response = client.get("/dashboard/_showcase")
    assert response.status_code == 404


def test_showcase_returns_200_with_dev_mode() -> None:
    """Showcase, dev mode aktifken 200 döndürmeli."""
    os.environ["CUSTOS_DEV_MODE"] = "true"
    try:
        response = client.get("/dashboard/_showcase")
        assert response.status_code == 200
        assert "Component Showcase" in response.text
    finally:
        os.environ.pop("CUSTOS_DEV_MODE", None)


def test_chart_config_invalid_key_returns_error() -> None:
    """Bilinmeyen chart_key icin 404 (DB varsa) ya da 503 (DB yoksa)."""
    response = client.get("/dashboard/overview/chart-config/invalid_chart")
    assert response.status_code in {404, 503}


def test_chart_config_post_invalid_key_returns_error() -> None:
    """Bilinmeyen chart_key POST icin 404 ya da 503."""
    response = client.post(
        "/dashboard/overview/chart-config/invalid_chart",
        data={"tag_ids": []},
    )
    assert response.status_code in {404, 503}


def test_chart_config_form_returns_200_or_error() -> None:
    """chart-config formu DB yoksa 503, chart yoksa 404, varsa 200."""
    response = client.get("/dashboard/overview/chart-config/sample-chart")
    assert response.status_code in {200, 404, 503}


def test_overview_chart_new_form_returns_200_or_503() -> None:
    """Yeni chart formu DB yoksa 503, varsa 200."""
    response = client.get("/dashboard/overview/charts/new")
    assert response.status_code in {200, 503}


def test_overview_chart_create_empty_title_returns_error() -> None:
    """Bos title ile yeni chart olusturulmaya calisilirsa 400 ya da 503."""
    response = client.post(
        "/dashboard/overview/charts",
        data={"title": "   ", "tag_ids": []},
        follow_redirects=False,
    )
    assert response.status_code in {400, 503}


def test_overview_chart_delete_nonexistent_returns_error() -> None:
    """Var olmayan chart silmeye calisilirsa 404 ya da 503."""
    response = client.post(
        "/dashboard/overview/charts/non-existent-slug/delete",
        follow_redirects=False,
    )
    assert response.status_code in {404, 503}


def test_overview_chart_detail_nonexistent_returns_error() -> None:
    """Var olmayan chart detay sayfasi 404 ya da 503."""
    response = client.get(
        "/dashboard/overview/charts/non-existent-slug/view",
    )
    assert response.status_code in {404, 503}


def test_overview_chart_time_window_invalid_returns_error() -> None:
    """Kabul edilmeyen zaman araligi icin 400 ya da 503."""
    response = client.post(
        "/dashboard/overview/charts/hvac-ahu/time-window",
        data={"minutes": "99999"},
        follow_redirects=False,
    )
    assert response.status_code in {400, 503}


def test_overview_chart_time_window_nonexistent_returns_error() -> None:
    """Var olmayan chart time-window 404 ya da 503."""
    response = client.post(
        "/dashboard/overview/charts/non-existent-slug/time-window",
        data={"minutes": "60"},
        follow_redirects=False,
    )
    assert response.status_code in {404, 503}
