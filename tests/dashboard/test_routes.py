"""Dashboard route testleri."""

from __future__ import annotations

import os

import pytest
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
