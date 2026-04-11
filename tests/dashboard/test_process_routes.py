"""Processes ve Templates dashboard route testleri."""

from __future__ import annotations

from fastapi.testclient import TestClient

from custos.__main__ import app

client = TestClient(app)


def test_processes_page_returns_503_or_200() -> None:
    """Processes sayfası DB varsa 200, yoksa 503 döndürmeli."""
    response = client.get("/dashboard/processes")
    assert response.status_code in (200, 503)


def test_process_detail_nonexistent_returns_error() -> None:
    """Var olmayan instance detayı 404 veya 503 döndürmeli."""
    response = client.get("/dashboard/processes/99999")
    assert response.status_code in (404, 503)


def test_process_delete_nonexistent_returns_error() -> None:
    """Var olmayan instance silme 404 veya 503 döndürmeli."""
    response = client.post("/dashboard/processes/99999/delete")
    assert response.status_code in (404, 503)


def test_templates_page_returns_503_or_200() -> None:
    """Templates sayfası DB varsa 200, yoksa 503 döndürmeli."""
    response = client.get("/dashboard/templates")
    assert response.status_code in (200, 503)


def test_template_detail_nonexistent_returns_error() -> None:
    """Var olmayan template detayı 404 veya 503 döndürmeli."""
    response = client.get("/dashboard/templates/99999")
    assert response.status_code in (404, 503)


def test_tag_reading_api_nonexistent_returns_error() -> None:
    """Var olmayan tag reading API 404 veya 503 döndürmeli."""
    response = client.get("/dashboard/api/tag-reading/NONEXISTENT_TAG_XYZ")
    assert response.status_code in (404, 503)
