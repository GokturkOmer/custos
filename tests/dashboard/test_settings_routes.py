"""Settings sayfası ve Push API route testleri."""

from __future__ import annotations

from fastapi.testclient import TestClient

from custos.__main__ import app

client = TestClient(app)


def test_settings_page_returns_200_or_503() -> None:
    """Settings sayfası 200 veya 503 döndürmeli (DB bağlantısına bağlı)."""
    response = client.get("/dashboard/settings")
    assert response.status_code in (200, 503)


def test_settings_page_contains_title() -> None:
    """Settings sayfası başlık içermeli."""
    response = client.get("/dashboard/settings")
    if response.status_code == 200:
        assert "Settings" in response.text
        assert "Sistem Bilgisi" in response.text


def test_vapid_public_key_api() -> None:
    """VAPID public key endpoint'i 200 döndürmeli."""
    response = client.get("/dashboard/api/push/vapid-public-key")
    assert response.status_code == 200
    data = response.json()
    assert "public_key" in data


def test_push_subscribe_missing_fields() -> None:
    """Eksik alanlarla subscribe 400 döndürmeli."""
    response = client.post(
        "/dashboard/api/push/subscribe",
        json={"endpoint": "https://test.example.com/push"},
    )
    # DB yoksa 503, alan eksikse 400
    assert response.status_code in (400, 503)


def test_push_unsubscribe_missing_endpoint() -> None:
    """Eksik endpoint ile unsubscribe 400 döndürmeli."""
    response = client.request(
        "DELETE",
        "/dashboard/api/push/subscribe",
        json={},
    )
    # DB yoksa 503, alan eksikse 400
    assert response.status_code in (400, 503)


def test_push_test_without_vapid() -> None:
    """VAPID yokken test bildirimi 400 döndürmeli."""
    response = client.post("/dashboard/api/push/test")
    # VAPID yoksa 400, DB yoksa 503
    assert response.status_code in (400, 503)
