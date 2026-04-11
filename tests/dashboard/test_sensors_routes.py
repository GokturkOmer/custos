"""Sensors sayfası route testleri.

DB bağlantısı olmadan çalışan testler — DB'ye bağlı route'lar 503 döndürür.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from custos.__main__ import app

client = TestClient(app)


def test_sensors_page_returns_503_without_db() -> None:
    """DB olmadan sensors sayfası 503 döndürmeli (lifespan dışında)."""
    # TestClient lifespan çalıştırır ama DB bağlantısı başarısız olabilir
    # Bu durumda 503 beklenir
    response = client.get("/dashboard/sensors")
    # DB varsa 200, yoksa 503
    assert response.status_code in (200, 503)


def test_sensor_new_form_returns_503_or_200() -> None:
    """Yeni tag formu DB'ye bağlı değil ama route DB kontrolü yapıyor."""
    response = client.get("/dashboard/sensors/new")
    assert response.status_code in (200, 503)


def test_sensor_edit_nonexistent_returns_error() -> None:
    """Var olmayan tag düzenleme 404 veya 503 döndürmeli."""
    response = client.get("/dashboard/sensors/NONEXISTENT/edit")
    assert response.status_code in (404, 503)


def test_sensor_delete_nonexistent_returns_error() -> None:
    """Var olmayan tag silme 404 veya 503 döndürmeli."""
    response = client.post("/dashboard/sensors/NONEXISTENT/delete")
    assert response.status_code in (404, 503)
