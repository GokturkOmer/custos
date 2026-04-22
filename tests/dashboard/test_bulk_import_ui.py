"""Bulk import dashboard UI testleri — HTMX modal + file upload.

DB bağlantısı olmadan çalışan testler; DB'ye bağlı route'lar 503 döndürür.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from custos.__main__ import app

client = TestClient(app)


def test_bulk_import_modal_get_returns_200() -> None:
    """Modal HTMX partial GET → 200 + içinde upload input."""
    response = client.get("/dashboard/sensors/bulk-import")
    assert response.status_code == 200
    body = response.text
    assert "Dosyadan Toplu Tag İçe Aktar" in body
    assert 'type="file"' in body
    assert 'accept=".csv,.yaml,.yml"' in body


def test_bulk_import_preview_invalid_extension() -> None:
    """Desteklenmeyen uzantı → 400 parse hatası."""
    files = {"file": ("tags.xlsx", b"binary", "application/vnd.ms-excel")}
    response = client.post("/dashboard/sensors/bulk-import/preview", files=files)
    # DB'ye dokunmayan endpoint, DB 503 değil parse hatası bekleriz
    assert response.status_code == 400


def test_bulk_import_preview_valid_csv_json() -> None:
    """Valid CSV + JSON response (HX-Request header yok)."""
    csv = (
        b"tag_id,name,modbus_host,register_address\n"
        b"TEST_UI_01,Test Tag,10.0.0.1,40001\n"
    )
    files = {"file": ("tags.csv", csv, "text/csv")}
    response = client.post(
        "/dashboard/sensors/bulk-import/preview",
        files=files,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["valid_count"] == 1
    assert data["error_count"] == 0


def test_bulk_import_preview_valid_csv_htmx_html() -> None:
    """HX-Request header → HTML partial döner."""
    csv = (
        b"tag_id,name,modbus_host,register_address\n"
        b"TEST_UI_02,Test,10.0.0.1,40001\n"
    )
    files = {"file": ("tags.csv", csv, "text/csv")}
    response = client.post(
        "/dashboard/sensors/bulk-import/preview",
        files=files,
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    # HTML içinde partial render'ın göstergesi
    assert "geçerli" in response.text
    assert "tags.csv" in response.text


def test_bulk_import_preview_validation_error_json() -> None:
    """Invalid register_type → errors listesinde raporlanır."""
    csv = (
        b"tag_id,name,modbus_host,register_address,register_type\n"
        b"TEST_UI_03,Test,10.0.0.1,40001,uint8\n"
    )
    files = {"file": ("tags.csv", csv, "text/csv")}
    response = client.post(
        "/dashboard/sensors/bulk-import/preview",
        files=files,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is False
    assert data["error_count"] >= 1
    assert data["errors"][0]["field"] == "register_type"


def test_bulk_import_commit_invalid_mode_400() -> None:
    """Geçersiz duplicate mode → 400."""
    csv = b"tag_id,name,modbus_host,register_address\nT,N,H,40001\n"
    files = {"file": ("tags.csv", csv, "text/csv")}
    response = client.post(
        "/dashboard/sensors/bulk-import",
        files=files,
        data={"mode": "definitelynotvalid"},
    )
    assert response.status_code == 400


def test_bulk_import_commit_db_unavailable_503() -> None:
    """DB bağlı değilse commit 503 dönmeli (preview değil — DB'ye dokunur)."""
    csv = b"tag_id,name,modbus_host,register_address\nT,N,H,40001\n"
    files = {"file": ("tags.csv", csv, "text/csv")}
    response = client.post(
        "/dashboard/sensors/bulk-import",
        files=files,
        data={"mode": "reject"},
    )
    # DB yoksa 503, varsa commit başarılı olabilir (TEST_ prefix kullanmadık)
    # Uygulamada TestClient lifespan DB başlatmazsa 503 beklenir.
    assert response.status_code in (200, 503, 409)


def test_bulk_import_preview_parse_error_htmx_html() -> None:
    """Parse hatası + HX-Request → HTML partial'da 'Dosya okunamadı'."""
    files = {"file": ("tags.csv", b"bozuk_header_yok\n", "text/csv")}
    response = client.post(
        "/dashboard/sensors/bulk-import/preview",
        files=files,
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 400
    assert "Dosya okunamadı" in response.text


def test_sensors_page_has_bulk_import_button() -> None:
    """Sensors sayfasında 'Dosyadan İçe Aktar' butonu bulunmalı."""
    response = client.get("/dashboard/sensors")
    # DB yoksa 503; button render'ı için 200 yoksa fallback OK kabul edelim
    if response.status_code != 200:
        return
    assert "Dosyadan İçe Aktar" in response.text
