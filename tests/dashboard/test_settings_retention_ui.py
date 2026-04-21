"""Settings — Veri Saklama UI smoke testleri (F11 Paket F).

TestClient smoke: /dashboard/settings render'ı, POST /settings/retention
validasyonu, GET /api/disk-usage partial'ı. DB bağlı değilse 503'e düşer;
testler her iki durumu da kabul eder (CI'de DB yok, pilot'ta var).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from custos.__main__ import app

client = TestClient(app)


def test_settings_retention_section_renders() -> None:
    """Settings sayfası retention başlığını içermeli (DB varsa)."""
    response = client.get("/dashboard/settings")
    assert response.status_code in (200, 503)
    if response.status_code == 200:
        # Veri Saklama bölümü başlığı ve radio etiketleri
        assert "Veri Saklama" in response.text
        assert "Ham veri saklama" in response.text
        assert "Sınırsız" in response.text
        # Arşiv kısmı
        assert "Şimdi arşivle" in response.text


def test_retention_form_invalid_mode_rejected() -> None:
    """Geçersiz retention_mode → 400 (DB yoksa 503)."""
    response = client.post(
        "/dashboard/settings/retention",
        data={"retention_mode": "bogus"},
        follow_redirects=False,
    )
    assert response.status_code in (400, 503)


def test_retention_form_accepts_valid_modes() -> None:
    """Geçerli mode'lar 303 (redirect) veya 503 (DB yok) döner."""
    for mode in ("30", "60", "180", "365", "off"):
        response = client.post(
            "/dashboard/settings/retention",
            data={"retention_mode": mode},
            follow_redirects=False,
        )
        assert response.status_code in (303, 503), (
            f"mode={mode} beklenmeyen status: {response.status_code}"
        )


def test_disk_usage_api_returns_partial() -> None:
    """GET /api/disk-usage HTML partial dönmeli (mount yoksa ölçülemedi)."""
    response = client.get("/dashboard/api/disk-usage")
    # Route her zaman 200 döner — mount yoksa "ölçülemedi" partial'ı gider
    assert response.status_code == 200
    # Partial mutlaka şu iki durumdan birini gösterir
    assert ("Disk Doluluk" in response.text) or (
        "ölçülemedi" in response.text
    )
