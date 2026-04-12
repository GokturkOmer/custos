"""KPI dashboard route testleri."""

from __future__ import annotations

from fastapi.testclient import TestClient

from custos.__main__ import app

client = TestClient(app)


def test_kpi_page_returns_200_or_503() -> None:
    """KPI sayfası 200 veya 503 döndürmeli (DB bağlantısına bağlı)."""
    response = client.get("/dashboard/kpi")
    assert response.status_code in (200, 503)


def test_kpi_page_contains_title() -> None:
    """KPI sayfası başlık içermeli."""
    response = client.get("/dashboard/kpi")
    if response.status_code == 200:
        assert "KPI" in response.text


def test_kpi_detail_nonexistent_returns_error() -> None:
    """Var olmayan instance detayı hata döndürmeli."""
    response = client.get("/dashboard/kpi/99999")
    # 404 (instance bulunamadı) veya 503 (DB yok)
    assert response.status_code in (404, 503)


def test_kpi_live_returns_200_or_503() -> None:
    """KPI live partial 200 veya 503 döndürmeli."""
    response = client.get("/dashboard/kpi/live")
    assert response.status_code in (200, 503)
