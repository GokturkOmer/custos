"""Dashboard FastAPI router.

Custos web arayüzünün ana router modülü. Jinja2 template'leri
ve statik dosyaları serve eder.
"""

from __future__ import annotations

import asyncio
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
from custos.analytics.scanner import ModbusScanner
from custos.shared.database import ConnectionProfile, DatabaseInterface, TagRecord

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
            {"href": "/dashboard/connections", "icon": "cpu", "label": "Connections"},
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
    """Sensors sayfası — tag listesi ve canlı değerler."""
    db = _get_db(request)
    tags = await db.list_tags()

    # Canlı değerleri çek
    tag_ids = [t.tag_id for t in tags]
    readings = await db.get_latest_tag_readings(tag_ids) if tag_ids else {}

    ctx = _base_context(
        page_title="Sensors",
        active_nav="/dashboard/sensors",
        tags=tags,
        readings=readings,
    )
    return templates.TemplateResponse(request, "pages/sensors.html", ctx)


@router.get("/sensors/live-values", response_class=HTMLResponse)
async def sensors_live_values(request: Request) -> HTMLResponse:
    """HTMX partial — sensor tablosu canlı değer güncellemesi."""
    db = _get_db(request)
    tags = await db.list_tags()
    tag_ids = [t.tag_id for t in tags]
    readings = await db.get_latest_tag_readings(tag_ids) if tag_ids else {}

    ctx = {"tags": tags, "readings": readings}
    return templates.TemplateResponse(
        request, "partials/sensor_live_rows.html", ctx,
    )


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

    # Modbus konvansiyonel adres (40001+) → 0-based protokol adresine çevir
    register_address_0based = (
        register_address - 40001 if register_address >= 40001 else register_address
    )

    # Preset'e göre interval hesapla
    if polling_preset != "custom":
        polling_interval_ms = POLLING_PRESETS.get(polling_preset, 10000)

    tag = TagRecord(
        tag_id=tag_id,
        name=name,
        modbus_host=modbus_host,
        modbus_port=modbus_port,
        unit_id=unit_id,
        register_address=register_address_0based,
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

    # Modbus konvansiyonel adres (40001+) → 0-based protokol adresine çevir
    register_address_0based = (
        register_address - 40001 if register_address >= 40001 else register_address
    )

    # Preset'e göre interval hesapla
    if polling_preset != "custom":
        polling_interval_ms = POLLING_PRESETS.get(polling_preset, 10000)

    updates: dict[str, object] = {
        "name": name,
        "modbus_host": modbus_host,
        "modbus_port": modbus_port,
        "unit_id": unit_id,
        "register_address": register_address_0based,
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


# --- Connection Profiles ---


@router.get("/connections", response_class=HTMLResponse)
async def connections_list(request: Request) -> HTMLResponse:
    """Connection Profiles sayfası — profil listesi."""
    db = _get_db(request)
    profiles = await db.list_connection_profiles()
    ctx = _base_context(
        page_title="Connections",
        active_nav="/dashboard/connections",
        profiles=profiles,
    )
    return templates.TemplateResponse(request, "pages/connections.html", ctx)


@router.get("/connections/new", response_class=HTMLResponse)
async def connection_new_form(request: Request) -> HTMLResponse:
    """Yeni connection profili formu."""
    ctx = _base_context(
        page_title="Yeni Connection Profili",
        active_nav="/dashboard/connections",
        profile=None,
        edit_mode=False,
    )
    return templates.TemplateResponse(request, "pages/connection_form.html", ctx)


@router.post("/connections", response_class=HTMLResponse)
async def connection_create(
    request: Request,
    name: str = Form(...),
    host: str = Form(...),
    port: int = Form(502),
    unit_id_start: int = Form(1),
    unit_id_end: int = Form(1),
) -> RedirectResponse:
    """Yeni connection profili oluşturur."""
    db = _get_db(request)

    profile = ConnectionProfile(
        name=name,
        host=host,
        port=port,
        unit_id_start=unit_id_start,
        unit_id_end=unit_id_end,
    )

    try:
        await db.insert_connection_profile(profile)
    except Exception:
        await logger.aerror("Connection profili oluşturma hatası", name=name, exc_info=True)
        raise HTTPException(status_code=400, detail="Profil oluşturulamadı") from None

    return RedirectResponse(url="/dashboard/connections", status_code=303)


@router.get("/connections/{profile_id:int}/edit", response_class=HTMLResponse)
async def connection_edit_form(request: Request, profile_id: int) -> HTMLResponse:
    """Connection profili düzenleme formu."""
    db = _get_db(request)
    profile = await db.get_connection_profile(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profil bulunamadı")

    ctx = _base_context(
        page_title=f"Düzenle: {profile.name}",
        active_nav="/dashboard/connections",
        profile=profile,
        edit_mode=True,
    )
    return templates.TemplateResponse(request, "pages/connection_form.html", ctx)


@router.post("/connections/{profile_id:int}/edit", response_class=HTMLResponse)
async def connection_update(
    request: Request,
    profile_id: int,
    name: str = Form(...),
    host: str = Form(...),
    port: int = Form(502),
    unit_id_start: int = Form(1),
    unit_id_end: int = Form(1),
) -> RedirectResponse:
    """Connection profilini günceller."""
    db = _get_db(request)

    updates: dict[str, object] = {
        "name": name,
        "host": host,
        "port": port,
        "unit_id_start": unit_id_start,
        "unit_id_end": unit_id_end,
    }

    result = await db.update_connection_profile(profile_id, updates)
    if result is None:
        raise HTTPException(status_code=404, detail="Profil bulunamadı")

    return RedirectResponse(url="/dashboard/connections", status_code=303)


@router.post("/connections/{profile_id:int}/delete", response_class=HTMLResponse)
async def connection_delete(request: Request, profile_id: int) -> RedirectResponse:
    """Connection profilini siler."""
    db = _get_db(request)
    deleted = await db.delete_connection_profile(profile_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Profil bulunamadı")

    return RedirectResponse(url="/dashboard/connections", status_code=303)


@router.post("/connections/{profile_id:int}/scan", response_class=HTMLResponse)
async def connection_scan(request: Request, profile_id: int) -> RedirectResponse:
    """Scan başlatır (arka plan task olarak)."""
    db = _get_db(request)
    profile = await db.get_connection_profile(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profil bulunamadı")

    scanner = ModbusScanner(profile=profile, database=db)

    # Scan task'ını oluştur ve app.state'te sakla (GC koruması)
    if not hasattr(request.app.state, "scan_tasks"):
        request.app.state.scan_tasks = {}

    task = asyncio.create_task(scanner.scan())
    scan_tasks: dict[int, asyncio.Task[Any]] = request.app.state.scan_tasks
    scan_tasks[profile_id] = task

    # Task tamamlandığında temizle
    def _cleanup(t: asyncio.Task[Any]) -> None:
        scan_tasks.pop(profile_id, None)

    task.add_done_callback(_cleanup)

    return RedirectResponse(
        url=f"/dashboard/connections/{profile_id}/scan-status",
        status_code=303,
    )


@router.get("/connections/{profile_id:int}/scan-status", response_class=HTMLResponse)
async def connection_scan_status(request: Request, profile_id: int) -> HTMLResponse:
    """Scan durumu sayfası (HTMX polling ile güncellenir).

    HTMX request'lerinde sadece kart partial'ını döndürür,
    normal request'lerde tam sayfayı render eder.
    """
    db = _get_db(request)
    profile = await db.get_connection_profile(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profil bulunamadı")

    # HTMX polling — sadece kart partial'ını döndür
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            request, "partials/scan_status_card.html", {"profile": profile},
        )

    # Normal sayfa yüklemesi — tam sayfa
    ctx = _base_context(
        page_title=f"Scan: {profile.name}",
        active_nav="/dashboard/connections",
        profile=profile,
    )
    return templates.TemplateResponse(request, "pages/scan_status.html", ctx)


# --- Scan Results ---


@router.get("/connections/{profile_id:int}/results", response_class=HTMLResponse)
async def connection_scan_results(request: Request, profile_id: int) -> HTMLResponse:
    """Scan sonuçları — keşfedilen tag'ler."""
    db = _get_db(request)
    profile = await db.get_connection_profile(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profil bulunamadı")

    # Bu profile ait discovered tag'leri getir
    all_discovered = await db.list_tags(status="discovered")
    discovered_tags = [
        t for t in all_discovered
        if t.modbus_host == profile.host and t.modbus_port == profile.port
    ]

    # Son değerleri çek
    tag_ids = [t.tag_id for t in discovered_tags]
    readings = await db.get_latest_tag_readings(tag_ids) if tag_ids else {}

    ctx = _base_context(
        page_title=f"Scan Sonuçları: {profile.name}",
        active_nav="/dashboard/connections",
        profile=profile,
        discovered_tags=discovered_tags,
        readings=readings,
    )
    return templates.TemplateResponse(request, "pages/scan_results.html", ctx)


@router.post("/connections/{profile_id:int}/results/activate", response_class=HTMLResponse)
async def connection_activate_tags(
    request: Request,
    profile_id: int,
) -> RedirectResponse:
    """Seçili tag'leri aktifleştirir."""
    db = _get_db(request)

    # Form'dan seçili tag_ids'leri al
    form = await request.form()
    tag_ids = form.getlist("tag_ids")

    for tag_id in tag_ids:
        tag_id_str = str(tag_id)
        await db.update_tag(tag_id_str, {
            "status": "active",
            "polling_interval_ms": 10000,
            "polling_preset": "slow",
        })

    await logger.ainfo(
        "Tag'ler aktifleştirildi",
        profil_id=profile_id,
        tag_sayısı=len(tag_ids),
    )

    return RedirectResponse(url="/dashboard/sensors", status_code=303)


@router.post("/connections/{profile_id:int}/results/ignore", response_class=HTMLResponse)
async def connection_ignore_tags(
    request: Request,
    profile_id: int,
) -> RedirectResponse:
    """Seçili tag'leri yok sayar."""
    db = _get_db(request)

    form = await request.form()
    tag_ids = form.getlist("tag_ids")

    for tag_id in tag_ids:
        tag_id_str = str(tag_id)
        await db.update_tag(tag_id_str, {"status": "ignored"})

    await logger.ainfo(
        "Tag'ler ignored olarak işaretlendi",
        profil_id=profile_id,
        tag_sayısı=len(tag_ids),
    )

    return RedirectResponse(
        url=f"/dashboard/connections/{profile_id}/results",
        status_code=303,
    )
