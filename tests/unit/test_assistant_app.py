"""Asistan servisi FastAPI app testleri (Faz 0 / karar A, D).

`/assistant/health` (auth'suz JSON 200), placeholder index ve `X-Custos-User`
middleware'inin uçtan uca çalıştığı doğrulanır. TestClient context manager
olarak KULLANILMAZ → lifespan (DB pool) tetiklenmez; testler DB'siz koşar.
"""

from __future__ import annotations

import base64
import json

from fastapi.testclient import TestClient

from custos.assistant.app import app

client = TestClient(app)


def _encode_user(payload: dict[str, object]) -> str:
    raw = json.dumps(payload).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def test_health_json_200() -> None:
    """/assistant/health → auth gerektirmez, JSON 200."""
    response = client.get("/assistant/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "custos-assistant"}


def test_index_bar_ve_panele_don_linki() -> None:
    """Placeholder index → "Custos · Asistan" bar + "← Panele dön" linki."""
    response = client.get("/assistant")
    assert response.status_code == 200
    assert "Custos · Asistan" in response.text
    assert "Panele dön" in response.text
    assert "/dashboard/overview" in response.text


def test_index_header_varsa_kullanici_adi_gosterilir() -> None:
    """Geçerli X-Custos-User → middleware parse eder, ad üst barda görünür."""
    header = _encode_user({"id": 7, "username": "Gökçe", "role": "operator"})
    response = client.get("/assistant", headers={"X-Custos-User": header})
    assert response.status_code == 200
    assert "Gökçe" in response.text


def test_index_header_yoksa_crash_yok() -> None:
    """Header yokken (request.state.user None) sayfa yine 200 döner."""
    response = client.get("/assistant")
    assert response.status_code == 200
    assert "Custos · Asistan" in response.text


def test_index_bozuk_header_crash_yok() -> None:
    """Bozuk X-Custos-User → user None'a düşer, 200 (servis düşmez)."""
    response = client.get("/assistant", headers={"X-Custos-User": "!!!bozuk!!!"})
    assert response.status_code == 200
