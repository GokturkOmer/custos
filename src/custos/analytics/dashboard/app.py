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
from custos.shared.database import (
    AssetInstance,
    ConnectionProfile,
    DatabaseInterface,
    TagBinding,
    TagRecord,
)

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
            {"href": "/dashboard/templates", "icon": "layers", "label": "Templates"},
            {"href": "/dashboard/processes", "icon": "package", "label": "Processes"},
            {"href": "#", "icon": "alert-triangle", "label": "Alarms"},
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


# --- Asset Templates (read-only) ---


@router.get("/templates", response_class=HTMLResponse)
async def templates_list(request: Request) -> HTMLResponse:
    """Template kütüphanesi — read-only liste."""
    db = _get_db(request)
    tmpl_list = await db.list_asset_templates()
    ctx = _base_context(
        page_title="Templates",
        active_nav="/dashboard/templates",
        templates_list=tmpl_list,
    )
    return templates.TemplateResponse(request, "pages/templates.html", ctx)


@router.get("/templates/{template_id:int}", response_class=HTMLResponse)
async def template_detail(request: Request, template_id: int) -> HTMLResponse:
    """Template detayı — roller ve KPI tanımları."""
    db = _get_db(request)
    tmpl = await db.get_asset_template(template_id)
    if tmpl is None:
        raise HTTPException(status_code=404, detail="Template bulunamadı")

    ctx = _base_context(
        page_title=f"Template: {tmpl.name}",
        active_nav="/dashboard/templates",
        tmpl=tmpl,
    )
    return templates.TemplateResponse(request, "pages/template_detail.html", ctx)


# --- Processes (Asset Instance CRUD) ---


@router.get("/processes", response_class=HTMLResponse)
async def processes_list(request: Request) -> HTMLResponse:
    """Asset instance listesi."""
    db = _get_db(request)
    instances = await db.list_asset_instances()
    tmpl_list = await db.list_asset_templates()
    tmpl_map = {t.id: t for t in tmpl_list}

    # Her instance için bağlı tag sayısını hesapla
    binding_counts: dict[int, int] = {}
    for inst in instances:
        assert inst.id is not None
        bindings = await db.list_tag_bindings(inst.id)
        binding_counts[inst.id] = len(bindings)

    ctx = _base_context(
        page_title="Processes",
        active_nav="/dashboard/processes",
        instances=instances,
        tmpl_map=tmpl_map,
        binding_counts=binding_counts,
    )
    return templates.TemplateResponse(request, "pages/processes.html", ctx)


@router.get("/processes/new", response_class=HTMLResponse)
async def process_new_form(request: Request) -> HTMLResponse:
    """Yeni asset instance formu."""
    db = _get_db(request)
    tmpl_list = await db.list_asset_templates()
    active_tags = await db.list_tags(status="active")

    ctx = _base_context(
        page_title="Yeni Asset Ekle",
        active_nav="/dashboard/processes",
        templates_list=tmpl_list,
        active_tags=active_tags,
        instance=None,
        edit_mode=False,
        existing_bindings={},
    )
    return templates.TemplateResponse(request, "pages/process_form.html", ctx)


@router.post("/processes", response_class=HTMLResponse)
async def process_create(request: Request) -> RedirectResponse:
    """Yeni asset instance + binding'ler oluşturur."""
    db = _get_db(request)
    form = await request.form()

    template_id = int(str(form.get("template_id", "0")))
    name = str(form.get("name", "")).strip()
    description = str(form.get("description", "")).strip()
    location = str(form.get("location", "")).strip()

    if not name or not template_id:
        raise HTTPException(status_code=400, detail="İsim ve template zorunlu")

    instance = AssetInstance(
        template_id=template_id,
        name=name,
        description=description,
        location=location,
    )

    try:
        created = await db.insert_asset_instance(instance)
    except Exception:
        await logger.aerror("Asset instance oluşturma hatası", name=name, exc_info=True)
        raise HTTPException(status_code=400, detail="Asset oluşturulamadı") from None

    # Binding'leri kaydet — form'da role_{role_id} = tag_id formatında
    assert created.id is not None
    tmpl = await db.get_asset_template(template_id)
    if tmpl is not None:
        bindings: list[TagBinding] = []
        for role in tmpl.roles:
            assert role.id is not None
            tag_id = str(form.get(f"role_{role.id}", "")).strip()
            if tag_id:
                bindings.append(TagBinding(
                    instance_id=created.id,
                    role_id=role.id,
                    tag_id=tag_id,
                ))
        if bindings:
            await db.replace_tag_bindings(created.id, bindings)

    return RedirectResponse(url=f"/dashboard/processes/{created.id}", status_code=303)


@router.get("/processes/{instance_id:int}", response_class=HTMLResponse)
async def process_detail(request: Request, instance_id: int) -> HTMLResponse:
    """Asset instance detay sayfası — canlı değerler."""
    db = _get_db(request)
    instance = await db.get_asset_instance(instance_id)
    if instance is None:
        raise HTTPException(status_code=404, detail="Asset bulunamadı")

    tmpl = await db.get_asset_template(instance.template_id)
    bindings = await db.list_tag_bindings(instance_id)

    # Role ve tag eşleştirmesi
    role_map = {r.id: r for r in (tmpl.roles if tmpl else [])}
    binding_data: list[dict[str, Any]] = []
    tag_ids: list[str] = []
    for b in bindings:
        role = role_map.get(b.role_id)
        binding_data.append({
            "role_label": role.label if role else "?",
            "unit_hint": role.unit_hint if role else "",
            "tag_id": b.tag_id,
        })
        tag_ids.append(b.tag_id)

    readings = await db.get_latest_tag_readings(tag_ids) if tag_ids else {}

    ctx = _base_context(
        page_title=f"Asset: {instance.name}",
        active_nav="/dashboard/processes",
        instance=instance,
        tmpl=tmpl,
        binding_data=binding_data,
        readings=readings,
    )
    return templates.TemplateResponse(request, "pages/process_detail.html", ctx)


@router.get("/processes/{instance_id:int}/live-values", response_class=HTMLResponse)
async def process_live_values(request: Request, instance_id: int) -> HTMLResponse:
    """HTMX partial — process detay canlı değer güncellemesi."""
    db = _get_db(request)
    instance = await db.get_asset_instance(instance_id)
    if instance is None:
        raise HTTPException(status_code=404, detail="Asset bulunamadı")

    tmpl = await db.get_asset_template(instance.template_id)
    bindings = await db.list_tag_bindings(instance_id)

    role_map = {r.id: r for r in (tmpl.roles if tmpl else [])}
    binding_data: list[dict[str, Any]] = []
    tag_ids: list[str] = []
    for b in bindings:
        role = role_map.get(b.role_id)
        binding_data.append({
            "role_label": role.label if role else "?",
            "unit_hint": role.unit_hint if role else "",
            "tag_id": b.tag_id,
        })
        tag_ids.append(b.tag_id)

    readings = await db.get_latest_tag_readings(tag_ids) if tag_ids else {}

    return templates.TemplateResponse(
        request, "partials/process_live_values.html",
        {"binding_data": binding_data, "readings": readings},
    )


@router.get("/processes/{instance_id:int}/edit", response_class=HTMLResponse)
async def process_edit_form(request: Request, instance_id: int) -> HTMLResponse:
    """Asset instance düzenleme formu."""
    db = _get_db(request)
    instance = await db.get_asset_instance(instance_id)
    if instance is None:
        raise HTTPException(status_code=404, detail="Asset bulunamadı")

    tmpl_list = await db.list_asset_templates()
    active_tags = await db.list_tags(status="active")
    bindings = await db.list_tag_bindings(instance_id)

    # Mevcut binding'leri role_id → tag_id haritası olarak hazırla
    existing_bindings = {b.role_id: b.tag_id for b in bindings}

    ctx = _base_context(
        page_title=f"Düzenle: {instance.name}",
        active_nav="/dashboard/processes",
        templates_list=tmpl_list,
        active_tags=active_tags,
        instance=instance,
        edit_mode=True,
        existing_bindings=existing_bindings,
    )
    return templates.TemplateResponse(request, "pages/process_form.html", ctx)


@router.post("/processes/{instance_id:int}/edit", response_class=HTMLResponse)
async def process_update(request: Request, instance_id: int) -> RedirectResponse:
    """Asset instance günceller."""
    db = _get_db(request)
    form = await request.form()

    name = str(form.get("name", "")).strip()
    description = str(form.get("description", "")).strip()
    location = str(form.get("location", "")).strip()
    status = str(form.get("status", "active")).strip()

    if not name:
        raise HTTPException(status_code=400, detail="İsim zorunlu")

    updates: dict[str, object] = {
        "name": name,
        "description": description,
        "location": location,
        "status": status,
    }
    result = await db.update_asset_instance(instance_id, updates)
    if result is None:
        raise HTTPException(status_code=404, detail="Asset bulunamadı")

    # Binding'leri güncelle
    instance = await db.get_asset_instance(instance_id)
    assert instance is not None
    tmpl = await db.get_asset_template(instance.template_id)
    if tmpl is not None:
        bindings: list[TagBinding] = []
        for role in tmpl.roles:
            assert role.id is not None
            tag_id = str(form.get(f"role_{role.id}", "")).strip()
            if tag_id:
                bindings.append(TagBinding(
                    instance_id=instance_id,
                    role_id=role.id,
                    tag_id=tag_id,
                ))
        await db.replace_tag_bindings(instance_id, bindings)

    return RedirectResponse(url=f"/dashboard/processes/{instance_id}", status_code=303)


@router.post("/processes/{instance_id:int}/delete", response_class=HTMLResponse)
async def process_delete(request: Request, instance_id: int) -> RedirectResponse:
    """Asset instance siler (binding'ler CASCADE ile silinir)."""
    db = _get_db(request)
    deleted = await db.delete_asset_instance(instance_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Asset bulunamadı")

    return RedirectResponse(url="/dashboard/processes", status_code=303)


# --- Tag Reading API (binding formu için) ---


@router.get("/api/tag-reading/{tag_id}")
async def api_tag_reading(request: Request, tag_id: str) -> dict[str, Any]:
    """Tek bir tag'in son okumasını JSON olarak döndürür."""
    db = _get_db(request)
    readings = await db.get_latest_tag_readings([tag_id])
    reading = readings.get(tag_id)
    if reading is None:
        raise HTTPException(status_code=404, detail="Okuma bulunamadı")

    return {
        "value": reading.value,
        "timestamp": reading.timestamp.isoformat(),
        "quality_flag": reading.quality_flag,
    }
