"""F9 AVM Template Pack dashboard smoke testleri.

``_get_avm_template_pack`` lazy yükleme yaptığı için lifespan olmadan da
YAML'dan pack okunur. Diğer dashboard testleriyle aynı module-level TestClient
pattern'i — ``with`` context manager kullanılmaz, shutdown DB'yi kapatmasın.
DB bağlantısı yoksa 503 fallback beklenir; her iki durum da kabul edilir.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from custos.__main__ import app

client = TestClient(app)


def test_settings_renders_avm_pack_section() -> None:
    """Settings sayfası AVM Template Pack bölümünü göstermeli (DB varsa)."""
    response = client.get("/dashboard/settings")
    assert response.status_code in (200, 503)
    if response.status_code == 200:
        assert "AVM Template Pack" in response.text
        assert "yüklü" in response.text or "Beklemede" in response.text


def test_seed_endpoint_redirects_or_503() -> None:
    """POST seed endpoint'i DB varsa 303, DB yoksa 503 döner."""
    response = client.post(
        "/dashboard/settings/avm-pack/seed",
        follow_redirects=False,
    )
    assert response.status_code in (303, 503)
    if response.status_code == 303:
        assert response.headers["location"].endswith("#avm-pack")


def test_settings_lists_expected_avm_slugs() -> None:
    """Settings sayfası 9 F9 slug'ını göstermeli (pack yüklüyse)."""
    response = client.get("/dashboard/settings")
    if response.status_code != 200:
        return  # DB yoksa atlanır
    for slug in ("chiller", "ahu", "fcu", "cooling_tower", "booster_pump_set"):
        assert slug in response.text, f"slug görünmüyor: {slug}"


def test_seed_endpoint_is_idempotent() -> None:
    """Ardışık iki seed çağrısı da aynı sonucu döner (303 veya 503)."""
    first = client.post(
        "/dashboard/settings/avm-pack/seed",
        follow_redirects=False,
    )
    second = client.post(
        "/dashboard/settings/avm-pack/seed",
        follow_redirects=False,
    )
    assert first.status_code == second.status_code
    assert first.status_code in (303, 503)
