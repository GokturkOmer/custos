"""Connection Profiles sayfası route testleri.

DB bağlantısı olmadan çalışan testler — DB'ye bağlı route'lar 503 döndürür.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from custos.__main__ import app

client = TestClient(app)


def test_connections_page_returns_503_or_200() -> None:
    """DB olmadan connections sayfası 503, varsa 200 döndürmeli."""
    response = client.get("/dashboard/connections")
    assert response.status_code in (200, 503)


def test_connection_new_form_returns_503_or_200() -> None:
    """Yeni profil formu sayfası."""
    response = client.get("/dashboard/connections/new")
    assert response.status_code in (200, 503)


def test_connection_edit_nonexistent_returns_error() -> None:
    """Var olmayan profil düzenleme 404 veya 503 döndürmeli."""
    response = client.get("/dashboard/connections/99999/edit")
    assert response.status_code in (404, 503)


def test_connection_delete_nonexistent_returns_error() -> None:
    """Var olmayan profil silme 404 veya 503 döndürmeli."""
    response = client.post("/dashboard/connections/99999/delete")
    assert response.status_code in (404, 503)


def test_connection_scan_nonexistent_returns_error() -> None:
    """Var olmayan profil için scan 404 veya 503 döndürmeli."""
    response = client.post("/dashboard/connections/99999/scan")
    assert response.status_code in (404, 503)


def test_connection_scan_status_nonexistent_returns_error() -> None:
    """Var olmayan profil scan durumu 404 veya 503 döndürmeli."""
    response = client.get("/dashboard/connections/99999/scan-status")
    assert response.status_code in (404, 503)


def test_connection_results_nonexistent_returns_error() -> None:
    """Var olmayan profil sonuçları 404 veya 503 döndürmeli."""
    response = client.get("/dashboard/connections/99999/results")
    assert response.status_code in (404, 503)
