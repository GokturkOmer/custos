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
    # VAPID yoksa 400, DB yoksa 503, auth yoksa 303 (login redirect)
    assert response.status_code in (303, 400, 503)


# --- P-03 yeni endpoint smoke testleri (auth/DB olmadan da pattern doğru) ---


def test_push_subscriptions_list_endpoint_exists() -> None:
    """P-03: GET /api/push/subscriptions endpoint'i tanımlı olmalı."""
    response = client.get("/dashboard/api/push/subscriptions")
    # Auth yoksa 303 redirect, DB yoksa 503, başarılıysa 200.
    # 404 dönerse endpoint hiç eklenmemiş demektir → fail.
    assert response.status_code in (200, 303, 503)


def test_push_master_switch_get_endpoint_exists() -> None:
    """P-03: GET /api/push/master-switch endpoint'i tanımlı olmalı (Operator+)."""
    response = client.get("/dashboard/api/push/master-switch")
    assert response.status_code in (200, 303, 503)


def test_push_master_switch_post_requires_developer() -> None:
    """P-03: POST /api/push/master-switch developer-only.

    Auth yokken 303 (login redirect); auth varsa Operator için 403.
    Hiçbir koşulda 200 dönmemeli (developer cookie'si olmadan).
    """
    response = client.post(
        "/dashboard/api/push/master-switch",
        json={"enabled": False},
    )
    assert response.status_code in (303, 403, 503)


def test_push_active_count_endpoint_exists() -> None:
    """P-03: GET /api/push/active-count developer-only footer rozet endpoint'i."""
    response = client.get("/dashboard/api/push/active-count")
    assert response.status_code in (200, 303, 403, 503)
