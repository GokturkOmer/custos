"""Dashboard FastAPI router.

Custos web arayüzünün ana router modülü. Jinja2 template'leri
ve statik dosyaları serve eder.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from custos.analytics.dashboard.fake_data import (
    get_overview_charts,
    get_overview_kpis,
    get_recent_alarms,
)

# Modül dizin yolları
_MODULE_DIR = Path(__file__).parent
_TEMPLATES_DIR = _MODULE_DIR / "templates"
_STATIC_DIR = _MODULE_DIR / "static"

router = APIRouter(prefix="/dashboard", tags=["dashboard"])
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


def get_static_files_app() -> StaticFiles:
    """Statik dosya uygulamasını döndürür."""
    return StaticFiles(directory=str(_STATIC_DIR))


def _is_dev_mode() -> bool:
    """Geliştirme modu aktif mi kontrol eder."""
    return os.environ.get("CUSTOS_DEV_MODE", "").lower() == "true"


def _base_context(**kwargs: Any) -> dict[str, Any]:
    """Tüm sayfalarda kullanılan temel template context'i oluşturur."""
    return {
        "version": "v0.1.0-dev",
        "nav_items": [
            {"href": "/dashboard/overview", "icon": "home", "label": "Overview"},
            {"href": "#", "icon": "activity", "label": "Sensors"},
            {"href": "#", "icon": "alert-triangle", "label": "Alarms"},
            {"href": "#", "icon": "sliders", "label": "Thresholds"},
            {"href": "#", "icon": "file-text", "label": "Logs"},
            {"href": "#", "icon": "settings", "label": "Settings"},
        ],
        **kwargs,
    }


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
