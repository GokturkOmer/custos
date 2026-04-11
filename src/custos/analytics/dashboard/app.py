"""Dashboard FastAPI router.

Custos web arayüzünün ana router modülü. Jinja2 template'leri
ve statik dosyaları serve eder.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from custos.analytics.dashboard.fake_data import (
    get_overview_charts,
    get_overview_kpis,
    get_recent_alarms,
)
from custos.shared.database import DatabaseInterface, TagRecord

logger = structlog.get_logger(logger_name="dashboard")

# Modül dizin yolları
_MODULE_DIR = Path(__file__).parent
_TEMPLATES_DIR = _MODULE_DIR / "templates"
_STATIC_DIR = _MODULE_DIR / "static"

router = APIRouter(prefix="/dashboard", tags=["dashboard"])
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

# Polling preset → ms eşleştirmesi
POLLING_PRESETS: dict[str, int] = {
    "slow": 10000,
    "normal": 1000,
    "fast": 100,
}


def get_static_files_app() -> StaticFiles:
    """Statik dosya uygulamasını döndürür."""
    return StaticFiles(directory=str(_STATIC_DIR))


def _is_dev_mode() -> bool:
    """Geliştirme modu aktif mi kontrol eder."""
    return os.environ.get("CUSTOS_DEV_MODE", "").lower() == "true"


def _get_db(request: Request) -> DatabaseInterface:
    """Request'ten DB instance'ını döndürür. Yoksa 503 fırlatır."""
    db: DatabaseInterface | None = getattr(request.app.state, "db", None)
    if db is None:
        raise HTTPException(status_code=503, detail="Veritabanı bağlantısı yok")
    return db


def _base_context(**kwargs: Any) -> dict[str, Any]:
    """Tüm sayfalarda kullanılan temel template context'i oluşturur."""
    return {
        "version": "v0.1.0-dev",
        "nav_items": [
            {"href": "/dashboard/overview", "icon": "home", "label": "Overview"},
            {"href": "/dashboard/sensors", "icon": "activity", "label": "Sensors"},
            {"href": "#", "icon": "alert-triangle", "label": "Alarms"},
            {"href": "#", "icon": "sliders", "label": "Thresholds"},
            {"href": "#", "icon": "file-text", "label": "Logs"},
            {"href": "#", "icon": "settings", "label": "Settings"},
        ],
        **kwargs,
    }


# --- Overview ---


@router.get("/", response_class=HTMLResponse)
async def dashboard_root() -> RedirectResponse:
    """Dashboard ana sayfası — overview'a yönlendirir."""
    return RedirectResponse(url="/dashboard/overview")


@router.get("/overview", response_class=HTMLResponse)
async def overview(request: Request) -> HTMLResponse:
    """Overview sayfası — ana kontrol paneli."""
    ctx = _base_context(
        page_title="Overview",
        active_nav="/dashboard/overview",
        kpis=get_overview_kpis(),
        charts=get_overview_charts(),
        alarms=get_recent_alarms(),
    )
    return templates.TemplateResponse(request, "pages/overview.html", ctx)


@router.get("/_showcase", response_class=HTMLResponse)
async def showcase(request: Request) -> HTMLResponse:
    """Component showcase sayfası (sadece geliştirme modunda)."""
    if not _is_dev_mode():
        raise HTTPException(status_code=404, detail="Sayfa bulunamadı")

    ctx = _base_context(
        page_title="Component Showcase",
        active_nav="",
    )
    return templates.TemplateResponse(request, "pages/_showcase.html", ctx)


# --- Sensors (Tag CRUD) ---


@router.get("/sensors", response_class=HTMLResponse)
async def sensors_list(request: Request) -> HTMLResponse:
    """Sensors sayfası — tag listesi."""
    db = _get_db(request)
    tags = await db.list_tags()
    ctx = _base_context(
        page_title="Sensors",
        active_nav="/dashboard/sensors",
        tags=tags,
    )
    return templates.TemplateResponse(request, "pages/sensors.html", ctx)


@router.get("/sensors/new", response_class=HTMLResponse)
async def sensor_new_form(request: Request) -> HTMLResponse:
    """Yeni tag ekleme formu."""
    ctx = _base_context(
        page_title="Yeni Tag Ekle",
        active_nav="/dashboard/sensors",
        tag=None,
        edit_mode=False,
    )
    return templates.TemplateResponse(request, "pages/sensor_form.html", ctx)


@router.post("/sensors", response_class=HTMLResponse)
async def sensor_create(
    request: Request,
    tag_id: str = Form(...),
    name: str = Form(...),
    modbus_host: str = Form(...),
    modbus_port: int = Form(502),
    unit_id: int = Form(1),
    register_address: int = Form(...),
    register_type: str = Form("uint16"),
    byte_order: str = Form("big"),
    gain: float = Form(1.0),
    offset: float = Form(0.0),
    unit: str = Form(""),
    polling_preset: str = Form("slow"),
    polling_interval_ms: int = Form(10000),
) -> RedirectResponse:
    """Yeni tag kaydı oluşturur."""
    db = _get_db(request)

    # Preset'e göre interval hesapla
    if polling_preset != "custom":
        polling_interval_ms = POLLING_PRESETS.get(polling_preset, 10000)

    tag = TagRecord(
        tag_id=tag_id,
        name=name,
        modbus_host=modbus_host,
        modbus_port=modbus_port,
        unit_id=unit_id,
        register_address=register_address,
        register_type=register_type,
        byte_order=byte_order,
        gain=gain,
        offset=offset,
        unit=unit,
        polling_interval_ms=polling_interval_ms,
        polling_preset=polling_preset,
    )

    try:
        await db.insert_tag(tag)
    except Exception:
        await logger.aerror("Tag oluşturma hatası", tag_id=tag_id, exc_info=True)
        raise HTTPException(status_code=400, detail="Tag oluşturulamadı") from None

    return RedirectResponse(url="/dashboard/sensors", status_code=303)


@router.get("/sensors/{tag_id}/edit", response_class=HTMLResponse)
async def sensor_edit_form(request: Request, tag_id: str) -> HTMLResponse:
    """Tag düzenleme formu."""
    db = _get_db(request)
    tag = await db.get_tag(tag_id)
    if tag is None:
        raise HTTPException(status_code=404, detail="Tag bulunamadı")

    ctx = _base_context(
        page_title=f"Düzenle: {tag.name}",
        active_nav="/dashboard/sensors",
        tag=tag,
        edit_mode=True,
    )
    return templates.TemplateResponse(request, "pages/sensor_form.html", ctx)


@router.post("/sensors/{tag_id}/edit", response_class=HTMLResponse)
async def sensor_update(
    request: Request,
    tag_id: str,
    name: str = Form(...),
    modbus_host: str = Form(...),
    modbus_port: int = Form(502),
    unit_id: int = Form(1),
    register_address: int = Form(...),
    register_type: str = Form("uint16"),
    byte_order: str = Form("big"),
    gain: float = Form(1.0),
    offset: float = Form(0.0),
    unit: str = Form(""),
    polling_preset: str = Form("slow"),
    polling_interval_ms: int = Form(10000),
    status: str = Form("active"),
) -> RedirectResponse:
    """Tag kaydını günceller."""
    db = _get_db(request)

    # Preset'e göre interval hesapla
    if polling_preset != "custom":
        polling_interval_ms = POLLING_PRESETS.get(polling_preset, 10000)

    updates: dict[str, object] = {
        "name": name,
        "modbus_host": modbus_host,
        "modbus_port": modbus_port,
        "unit_id": unit_id,
        "register_address": register_address,
        "register_type": register_type,
        "byte_order": byte_order,
        "gain": gain,
        "offset": offset,
        "unit": unit,
        "polling_interval_ms": polling_interval_ms,
        "polling_preset": polling_preset,
        "status": status,
    }

    result = await db.update_tag(tag_id, updates)
    if result is None:
        raise HTTPException(status_code=404, detail="Tag bulunamadı")

    return RedirectResponse(url="/dashboard/sensors", status_code=303)


@router.post("/sensors/{tag_id}/delete", response_class=HTMLResponse)
async def sensor_delete(request: Request, tag_id: str) -> RedirectResponse:
    """Tag kaydını siler."""
    db = _get_db(request)
    deleted = await db.delete_tag(tag_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Tag bulunamadı")

    return RedirectResponse(url="/dashboard/sensors", status_code=303)
