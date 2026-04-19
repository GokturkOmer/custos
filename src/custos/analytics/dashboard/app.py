"""Dashboard FastAPI router.

Custos web arayüzünün ana router modülü. Jinja2 template'leri
ve statik dosyaları serve eder.
"""

from __future__ import annotations

import asyncio
import os
import re
import unicodedata
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from custos.analytics.push_sender import send_push_notifications
from custos.analytics.scanner import ModbusScanner
from custos.shared.database import (
    AssetInstance,
    AuditLogEntry,
    ConnectionProfile,
    DatabaseInterface,
    OverviewChart,
    PushSubscription,
    TagBinding,
    TagRecord,
    Threshold,
)
from custos.shared.vapid import get_vapid_keys, is_push_enabled

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

# Overview sayfası için sabit timedelta'lar (her çağrıda nesne oluşturmamak için)
_timedelta_24h = timedelta(hours=24)
_timedelta_30m = timedelta(minutes=30)


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
            {"href": "/dashboard/kpi", "icon": "trending-up", "label": "KPI"},
            {"href": "/dashboard/alarms", "icon": "alert-triangle", "label": "Alarms"},
            {"href": "/dashboard/logs", "icon": "file-text", "label": "Logs"},
            {"href": "/dashboard/settings", "icon": "settings", "label": "Settings"},
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
    """Overview sayfası — ana kontrol paneli.

    Chart slotlari DB'den dinamik olarak gelir (overview_charts tablosu).
    Kullanici yeni chart ekleyip silebilir; her chart kendi tag seciminden
    beslenir.
    """
    # chart_key → { timestamps, series, labels, units }
    charts: dict[str, Any] = {}
    # Template'in render edecegi chart metadata (baslik + sort sirasi)
    chart_slots: list[dict[str, Any]] = []

    alarms: list[dict[str, Any]] = []
    active_alarm_count = 0
    total_tags = 0
    total_assets = 0
    anomaly_count_24h = 0
    db_instance: DatabaseInterface | None = getattr(request.app.state, "db", None)
    if db_instance is not None:
        recent_events = await db_instance.list_alarm_events(limit=10)
        triggered = await db_instance.list_alarm_events(state="triggered", limit=1000)
        acked = await db_instance.list_alarm_events(state="acknowledged", limit=1000)
        active_alarm_count = len(triggered) + len(acked)

        all_tags = await db_instance.list_tags()
        total_tags = len(all_tags)
        all_instances = await db_instance.list_asset_instances()
        total_assets = len(all_instances)

        since_24h = datetime.now(UTC) - _timedelta_24h
        anomaly_count_24h = await db_instance.count_anomalies(since=since_24h)

        # Dinamik chart listesi — DB'den (sort_order + created_at sirasi)
        overview_charts = await db_instance.list_overview_charts()
        all_chart_tags = await db_instance.list_overview_chart_tags()
        tags_by_chart: dict[str, list[str]] = {}
        for ct in all_chart_tags:
            tags_by_chart.setdefault(ct.chart_key, []).append(ct.tag_id)

        tag_map = {t.tag_id: t for t in all_tags}
        now = datetime.now(UTC)
        real_start = now - _timedelta_30m
        for oc in overview_charts:
            chart_slots.append({
                "chart_key": oc.chart_key,
                "title": oc.title,
                "sort_order": oc.sort_order,
            })
            tag_ids = tags_by_chart.get(oc.chart_key, [])
            if not tag_ids:
                charts[oc.chart_key] = {
                    "timestamps": [],
                    "series": [],
                    "labels": [],
                    "units": [],
                }
                continue

            series: list[list[float]] = []
            labels: list[str] = []
            units: list[str] = []
            timestamps: list[int] = []
            for tid in tag_ids:
                readings = await db_instance.query_tag_readings(
                    tid, real_start, now,
                )
                if readings:
                    tag_rec = tag_map.get(tid)
                    unit = tag_rec.unit if tag_rec else ""
                    unit_suffix = f" ({unit})" if unit else ""
                    labels.append(f"{tid}{unit_suffix}")
                    units.append(unit)
                    series.append([r.value for r in readings])
                    if not timestamps:
                        timestamps = [
                            int(r.timestamp.timestamp()) for r in readings
                        ]
            charts[oc.chart_key] = {
                "timestamps": timestamps,
                "series": series,
                "labels": labels,
                "units": units,
            }

        # Threshold adlarini cek
        thr_ids = {e.threshold_id for e in recent_events}
        thr_map: dict[int, Threshold] = {}
        for thr_id in thr_ids:
            t = await db_instance.get_threshold(thr_id)
            if t is not None:
                thr_map[thr_id] = t

        for event in recent_events:
            t = thr_map.get(event.threshold_id)
            state_label = {
                "triggered": "Critical",
                "acknowledged": "Warning",
                "cleared": "OK",
            }.get(event.state, event.state)
            state_status = {
                "triggered": "crit",
                "acknowledged": "warn",
                "cleared": "ok",
            }.get(event.state, "neutral")
            alarms.append({
                "time": (
                    event.triggered_at.strftime("%H:%M:%S")
                    if event.triggered_at else "-"
                ),
                "sensor": event.tag_id,
                "type": t.name if t else f"Threshold #{event.threshold_id}",
                "status": state_status,
                "status_label": state_label,
            })

    # Gerçek KPI kartları
    kpis: list[dict[str, str]] = [
        {
            "label": "Active Alarms",
            "value": str(active_alarm_count),
            "status": "crit" if active_alarm_count > 0 else "ok",
            "delta": "",
        },
        {
            "label": "Total Tags",
            "value": str(total_tags),
            "status": "neutral",
            "delta": "",
        },
        {
            "label": "Total Assets",
            "value": str(total_assets),
            "status": "neutral",
            "delta": "",
        },
        {
            "label": "Anomalies (24h)",
            "value": str(anomaly_count_24h),
            "status": "warn" if anomaly_count_24h > 0 else "ok",
            "delta": "",
        },
    ]

    ctx = _base_context(
        page_title="Overview",
        active_nav="/dashboard/overview",
        kpis=kpis,
        charts=charts,
        chart_slots=chart_slots,
        alarms=alarms,
    )
    return templates.TemplateResponse(request, "pages/overview.html", ctx)


# --- Overview Chart Slotlari (dinamik) ---

# Slug uretiminde kullanilan Turkce karakter eslestirmesi
_SLUG_TR_MAP = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")


def slugify_chart_title(title: str) -> str:
    """Chart basligini URL-safe slug'a cevirir.

    Ornekler:
        "Sirkülasyon Pompası #1" → "sirkulasyon-pompasi-1"
        "HVAC / AHU"              → "hvac-ahu"
    """
    s = title.translate(_SLUG_TR_MAP)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    return s or "chart"


async def _unique_chart_key(base: str, db: DatabaseInterface) -> str:
    """Base slug varsa -2, -3 gibi suffix ekleyerek benzersiz chart_key uretir."""
    candidate = base
    n = 2
    while await db.get_overview_chart(candidate) is not None:
        candidate = f"{base}-{n}"
        n += 1
    return candidate


@router.get("/overview/charts/new", response_class=HTMLResponse)
async def overview_chart_new_form(request: Request) -> HTMLResponse:
    """Yeni chart olusturma formu."""
    db = _get_db(request)
    all_tags = await db.list_tags(status="active")
    ctx = _base_context(
        page_title="Yeni Chart",
        active_nav="/dashboard/overview",
        all_tags=all_tags,
    )
    return templates.TemplateResponse(
        request, "pages/overview_chart_form.html", ctx,
    )


@router.post("/overview/charts", response_class=HTMLResponse)
async def overview_chart_create(
    request: Request,
    title: str = Form(...),
) -> RedirectResponse:
    """Yeni chart slotu olusturur ve secili tag'leri baglar."""
    db = _get_db(request)
    clean_title = title.strip()
    if not clean_title:
        raise HTTPException(status_code=400, detail="Baslik bos olamaz")

    base_slug = slugify_chart_title(clean_title)
    chart_key = await _unique_chart_key(base_slug, db)

    existing = await db.list_overview_charts()
    sort_order = max((c.sort_order for c in existing), default=-1) + 1

    await db.insert_overview_chart(OverviewChart(
        chart_key=chart_key, title=clean_title, sort_order=sort_order,
    ))

    form = await request.form()
    tag_ids = [str(t) for t in form.getlist("tag_ids")]
    if tag_ids:
        await db.replace_overview_chart_tags(chart_key, tag_ids)

    await db.insert_audit_log(AuditLogEntry(
        category="chart_config", action="create",
        detail=f"{chart_key}: {clean_title} ({len(tag_ids)} tag)",
    ))
    return RedirectResponse(url="/dashboard/overview", status_code=303)


@router.post("/overview/charts/{chart_key}/delete", response_class=HTMLResponse)
async def overview_chart_delete(
    request: Request, chart_key: str,
) -> RedirectResponse:
    """Chart slotunu siler; tag bindingleri FK CASCADE ile duser."""
    db = _get_db(request)
    ok = await db.delete_overview_chart(chart_key)
    if not ok:
        raise HTTPException(status_code=404, detail="Chart bulunamadi")
    await db.insert_audit_log(AuditLogEntry(
        category="chart_config", action="delete", detail=chart_key,
    ))
    return RedirectResponse(url="/dashboard/overview", status_code=303)


@router.get("/overview/chart-config/{chart_key}", response_class=HTMLResponse)
async def chart_config_form(request: Request, chart_key: str) -> HTMLResponse:
    """Grafik tag secim formunu HTMX partial olarak dondurur."""
    db = _get_db(request)
    chart = await db.get_overview_chart(chart_key)
    if chart is None:
        raise HTTPException(status_code=404, detail="Gecersiz grafik slotu")

    all_tags = await db.list_tags(status="active")
    chart_tags = await db.list_overview_chart_tags(chart_key)
    selected_ids = {ct.tag_id for ct in chart_tags}

    ctx = {
        "request": request,
        "chart_key": chart_key,
        "chart_title": chart.title,
        "all_tags": all_tags,
        "selected_ids": selected_ids,
    }
    return templates.TemplateResponse(
        request, "partials/chart_tag_selector.html", ctx,
    )


@router.post("/overview/chart-config/{chart_key}", response_class=HTMLResponse)
async def chart_config_save(request: Request, chart_key: str) -> RedirectResponse:
    """Grafik tag secimini kaydeder ve overview'a yonlendirir."""
    db = _get_db(request)
    chart = await db.get_overview_chart(chart_key)
    if chart is None:
        raise HTTPException(status_code=404, detail="Gecersiz grafik slotu")

    form = await request.form()
    tag_ids = form.getlist("tag_ids")
    await db.replace_overview_chart_tags(chart_key, [str(t) for t in tag_ids])

    await db.insert_audit_log(AuditLogEntry(
        category="chart_config",
        action="update",
        detail=f"{chart_key}: {len(tag_ids)} tag secildi",
    ))
    return RedirectResponse(url="/dashboard/overview", status_code=303)


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

    # KPI özet kartları
    kpi_cards: list[dict[str, Any]] = []
    if tmpl and tmpl.kpi_definitions:
        latest_kpis = await db.get_latest_kpi_results(instance_id)
        for kd in tmpl.kpi_definitions:
            result = latest_kpis.get(kd.id) if kd.id is not None else None
            kpi_cards.append({
                "name": kd.name,
                "value": result.value if result else None,
                "unit": kd.unit,
            })

    ctx = _base_context(
        page_title=f"Asset: {instance.name}",
        active_nav="/dashboard/processes",
        instance=instance,
        tmpl=tmpl,
        binding_data=binding_data,
        readings=readings,
        kpi_cards=kpi_cards,
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


# --- KPI ---


@router.get("/kpi", response_class=HTMLResponse)
async def kpi_list(request: Request, instance_id: int | None = None) -> HTMLResponse:
    """KPI listesi sayfası — tüm aktif instance'ların KPI değerleri."""
    db = _get_db(request)
    instances = await db.list_asset_instances(status="active")

    # KPI satırlarını oluştur
    kpi_rows = await _build_kpi_rows(db, instances, instance_id)

    ctx = _base_context(
        page_title="KPI",
        active_nav="/dashboard/kpi",
        instances=instances,
        kpi_rows=kpi_rows,
        filter_instance_id=instance_id,
    )
    return templates.TemplateResponse(request, "pages/kpi.html", ctx)


@router.get("/kpi/live", response_class=HTMLResponse)
async def kpi_live(request: Request, instance_id: int | None = None) -> HTMLResponse:
    """HTMX partial — KPI değerleri güncelleme."""
    db = _get_db(request)
    instances = await db.list_asset_instances(status="active")
    kpi_rows = await _build_kpi_rows(db, instances, instance_id)
    ctx = {"kpi_rows": kpi_rows}
    return templates.TemplateResponse(request, "partials/kpi_live.html", ctx)


@router.get("/kpi/{instance_id:int}", response_class=HTMLResponse)
async def kpi_detail(request: Request, instance_id: int) -> HTMLResponse:
    """Instance KPI detay sayfası — trend grafikleri ve anomali skoru."""
    db = _get_db(request)
    instance = await db.get_asset_instance(instance_id)
    if instance is None:
        raise HTTPException(status_code=404, detail="Instance bulunamadı")

    tmpl = await db.get_asset_template(instance.template_id)
    kpi_definitions = tmpl.kpi_definitions if tmpl else []

    # En son KPI değerleri
    latest_kpis = await db.get_latest_kpi_results(instance_id)
    kpi_summaries: list[dict[str, Any]] = []
    for kd in kpi_definitions:
        result = latest_kpis.get(kd.id) if kd.id is not None else None
        kpi_summaries.append({
            "name": kd.name,
            "value": result.value if result else None,
            "unit": kd.unit,
            "formula": kd.formula,
        })

    # Anomali skoru
    anomaly_score = await db.get_latest_anomaly_score(instance_id)
    anomaly_history = await db.list_anomaly_scores(instance_id, limit=50)

    # KPI trend grafik verisi — en son 60 sonuç (yaklaşık 1 saat)
    charts: dict[str, Any] = {}
    if kpi_definitions:
        kpi_series: list[list[float]] = []
        kpi_labels: list[str] = []
        kpi_timestamps: list[int] = []
        for kd in kpi_definitions:
            if kd.id is None:
                continue
            results = await db.list_kpi_results(instance_id, kd.id, limit=60)
            if results:
                results.reverse()  # eski → yeni sıra
                kpi_labels.append(f"{kd.name} ({kd.unit})")
                kpi_series.append([r.value for r in results])
                if not kpi_timestamps:
                    kpi_timestamps = [int(r.bucket_start.timestamp()) for r in results]
        if kpi_timestamps and kpi_series:
            charts["kpi_chart"] = {
                "timestamps": kpi_timestamps,
                "series": kpi_series,
                "labels": kpi_labels,
            }

    ctx = _base_context(
        page_title=f"KPI: {instance.name}",
        active_nav="/dashboard/kpi",
        instance=instance,
        tmpl=tmpl,
        kpi_summaries=kpi_summaries,
        kpi_definitions=kpi_definitions,
        anomaly_score=anomaly_score,
        anomaly_history=anomaly_history,
        charts=charts,
    )
    return templates.TemplateResponse(request, "pages/kpi_detail.html", ctx)


async def _build_kpi_rows(
    db: DatabaseInterface,
    instances: list[AssetInstance],
    filter_instance_id: int | None,
) -> list[dict[str, Any]]:
    """KPI tablosu için satır verisi oluşturur."""
    kpi_rows: list[dict[str, Any]] = []
    for inst in instances:
        if filter_instance_id is not None and inst.id != filter_instance_id:
            continue
        assert inst.id is not None
        tmpl = await db.get_asset_template(inst.template_id)
        if tmpl is None or not tmpl.kpi_definitions:
            continue

        latest_kpis = await db.get_latest_kpi_results(inst.id)
        anomaly = await db.get_latest_anomaly_score(inst.id)

        for kd in tmpl.kpi_definitions:
            result = latest_kpis.get(kd.id) if kd.id is not None else None
            anomaly_status = "unknown"
            if anomaly is not None:
                anomaly_status = "anomaly" if anomaly.is_anomaly else "normal"

            kpi_rows.append({
                "instance_id": inst.id,
                "instance_name": inst.name,
                "kpi_name": kd.name,
                "value": result.value if result else 0.0,
                "unit": kd.unit,
                "anomaly_status": anomaly_status,
            })
    return kpi_rows


# --- Threshold CRUD ---


@router.get("/thresholds", response_class=HTMLResponse)
async def thresholds_list(request: Request) -> HTMLResponse:
    """Threshold listesi sayfası."""
    db = _get_db(request)
    thresholds = await db.list_thresholds()
    ctx = _base_context(
        page_title="Thresholds",
        active_nav="/dashboard/alarms",
        thresholds=thresholds,
    )
    return templates.TemplateResponse(request, "pages/thresholds.html", ctx)


@router.get("/thresholds/new", response_class=HTMLResponse)
async def threshold_new(request: Request) -> HTMLResponse:
    """Yeni threshold formu."""
    db = _get_db(request)
    tags = await db.list_tags(status="active")
    ctx = _base_context(
        page_title="Yeni Threshold",
        active_nav="/dashboard/alarms",
        tags=tags,
        edit_mode=False,
    )
    return templates.TemplateResponse(request, "pages/threshold_form.html", ctx)


@router.post("/thresholds", response_class=HTMLResponse)
async def threshold_create(
    request: Request,
    tag_id: str = Form(...),
    name: str = Form(...),
    direction: str = Form("high"),
    set_point: float = Form(...),
    severity: str = Form("warn"),
    debounce_seconds: int = Form(5),
    hysteresis: float = Form(0.0),
) -> RedirectResponse:
    """Yeni threshold kaydı oluşturur."""
    db = _get_db(request)
    threshold = Threshold(
        tag_id=tag_id,
        name=name,
        direction=direction,
        set_point=set_point,
        severity=severity,
        debounce_seconds=debounce_seconds,
        hysteresis=hysteresis,
    )
    created = await db.insert_threshold(threshold)

    # Audit log
    await db.insert_audit_log(
        AuditLogEntry(
            category="alarm",
            action="threshold_created",
            entity_type="threshold",
            entity_id=str(created.id),
            detail=f"Threshold oluşturuldu: {name} (tag={tag_id}, set_point={set_point})",
        ),
    )

    return RedirectResponse(url="/dashboard/thresholds", status_code=303)


@router.get("/thresholds/{threshold_id:int}/edit", response_class=HTMLResponse)
async def threshold_edit(request: Request, threshold_id: int) -> HTMLResponse:
    """Threshold düzenleme formu."""
    db = _get_db(request)
    threshold = await db.get_threshold(threshold_id)
    if threshold is None:
        raise HTTPException(status_code=404, detail="Threshold bulunamadı")

    tags = await db.list_tags(status="active")
    ctx = _base_context(
        page_title=f"Düzenle: {threshold.name}",
        active_nav="/dashboard/alarms",
        threshold=threshold,
        tags=tags,
        edit_mode=True,
    )
    return templates.TemplateResponse(request, "pages/threshold_form.html", ctx)


@router.post("/thresholds/{threshold_id:int}/edit", response_class=HTMLResponse)
async def threshold_update(
    request: Request,
    threshold_id: int,
    name: str = Form(...),
    direction: str = Form("high"),
    set_point: float = Form(...),
    severity: str = Form("warn"),
    debounce_seconds: int = Form(5),
    hysteresis: float = Form(0.0),
) -> RedirectResponse:
    """Threshold kaydını günceller."""
    db = _get_db(request)
    updates: dict[str, object] = {
        "name": name,
        "direction": direction,
        "set_point": set_point,
        "severity": severity,
        "debounce_seconds": debounce_seconds,
        "hysteresis": hysteresis,
    }
    result = await db.update_threshold(threshold_id, updates)
    if result is None:
        raise HTTPException(status_code=404, detail="Threshold bulunamadı")

    return RedirectResponse(url="/dashboard/thresholds", status_code=303)


@router.post("/thresholds/{threshold_id:int}/delete", response_class=HTMLResponse)
async def threshold_delete(
    request: Request,
    threshold_id: int,
) -> RedirectResponse:
    """Threshold kaydını siler."""
    db = _get_db(request)

    # Silmeden önce adını al (audit log için)
    threshold = await db.get_threshold(threshold_id)
    deleted = await db.delete_threshold(threshold_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Threshold bulunamadı")

    # Audit log
    await db.insert_audit_log(
        AuditLogEntry(
            category="alarm",
            action="threshold_deleted",
            entity_type="threshold",
            entity_id=str(threshold_id),
            detail=f"Threshold silindi: {threshold.name if threshold else threshold_id}",
        ),
    )

    return RedirectResponse(url="/dashboard/thresholds", status_code=303)


@router.post("/thresholds/{threshold_id:int}/toggle", response_class=HTMLResponse)
async def threshold_toggle(
    request: Request,
    threshold_id: int,
) -> RedirectResponse:
    """Threshold enable/disable toggle."""
    db = _get_db(request)
    threshold = await db.get_threshold(threshold_id)
    if threshold is None:
        raise HTTPException(status_code=404, detail="Threshold bulunamadı")

    new_state = not threshold.enabled
    await db.update_threshold(threshold_id, {"enabled": new_state})

    return RedirectResponse(url="/dashboard/thresholds", status_code=303)


# --- Alarms ---


@router.get("/alarms", response_class=HTMLResponse)
async def alarms_page(request: Request) -> HTMLResponse:
    """Alarm sayfası — aktif alarmlar ve geçmiş."""
    db = _get_db(request)

    # Aktif alarmlar (triggered + acknowledged)
    triggered = await db.list_alarm_events(state="triggered", limit=100)
    acknowledged = await db.list_alarm_events(state="acknowledged", limit=100)
    active_alarms = triggered + acknowledged
    # Tetiklenme zamanına göre sırala (en yeni üstte)
    active_alarms.sort(
        key=lambda a: a.triggered_at or datetime.min.replace(tzinfo=None),
        reverse=True,
    )

    # Geçmiş (cleared)
    cleared_alarms = await db.list_alarm_events(state="cleared", limit=50)

    # Threshold adları ve severity'leri çek (denormalize bilgi için)
    threshold_ids = {
        a.threshold_id for a in active_alarms + cleared_alarms
    }
    threshold_names: dict[int, str] = {}
    threshold_severities: dict[int, str] = {}
    for tid in threshold_ids:
        t = await db.get_threshold(tid)
        if t is not None:
            threshold_names[tid] = t.name
            threshold_severities[tid] = t.severity

    ctx = _base_context(
        page_title="Alarms",
        active_nav="/dashboard/alarms",
        active_alarms=active_alarms,
        cleared_alarms=cleared_alarms,
        threshold_names=threshold_names,
        threshold_severities=threshold_severities,
    )
    return templates.TemplateResponse(request, "pages/alarms.html", ctx)


@router.get("/alarms/active", response_class=HTMLResponse)
async def alarms_active_partial(request: Request) -> HTMLResponse:
    """HTMX partial — aktif alarm satırları."""
    db = _get_db(request)

    triggered = await db.list_alarm_events(state="triggered", limit=100)
    acknowledged = await db.list_alarm_events(state="acknowledged", limit=100)
    active_alarms = triggered + acknowledged
    active_alarms.sort(
        key=lambda a: a.triggered_at or datetime.min.replace(tzinfo=None),
        reverse=True,
    )

    threshold_ids = {a.threshold_id for a in active_alarms}
    threshold_names: dict[int, str] = {}
    threshold_severities: dict[int, str] = {}
    for tid in threshold_ids:
        t = await db.get_threshold(tid)
        if t is not None:
            threshold_names[tid] = t.name
            threshold_severities[tid] = t.severity

    ctx = {
        "active_alarms": active_alarms,
        "threshold_names": threshold_names,
        "threshold_severities": threshold_severities,
    }
    return templates.TemplateResponse(
        request, "partials/alarm_active_rows.html", ctx,
    )


@router.post("/alarms/{event_id:int}/acknowledge", response_class=HTMLResponse)
async def alarm_acknowledge(
    request: Request,
    event_id: int,
) -> RedirectResponse:
    """Alarm acknowledge — triggered → acknowledged."""
    db = _get_db(request)
    event = await db.get_alarm_event(event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Alarm bulunamadı")

    if event.state != "triggered":
        raise HTTPException(
            status_code=400,
            detail="Sadece 'triggered' durumundaki alarmlar onaylanabilir",
        )

    now = datetime.now(UTC)
    await db.update_alarm_event(
        event_id,
        {"state": "acknowledged", "acknowledged_at": now},
    )

    # Audit log
    await db.insert_audit_log(
        AuditLogEntry(
            category="alarm",
            action="acknowledged",
            entity_type="alarm_event",
            entity_id=str(event_id),
            detail=(
                f"Alarm onaylandı: threshold_id={event.threshold_id}, "
                f"tag={event.tag_id}"
            ),
        ),
    )

    return RedirectResponse(url="/dashboard/alarms", status_code=303)


# --- Logs ---


@router.get("/logs", response_class=HTMLResponse)
async def logs_page(request: Request) -> HTMLResponse:
    """Audit log sayfası."""
    db = _get_db(request)
    category = request.query_params.get("category") or None

    entries = await db.list_audit_log(category=category, limit=50, offset=0)
    total = await db.count_audit_log(category=category)
    has_more = total > 50

    ctx = _base_context(
        page_title="Logs",
        active_nav="/dashboard/logs",
        entries=entries,
        active_category=category or "",
        has_more=has_more,
        next_offset=50,
    )
    return templates.TemplateResponse(request, "pages/logs.html", ctx)


@router.get("/logs/entries", response_class=HTMLResponse)
async def logs_entries_partial(request: Request) -> HTMLResponse:
    """HTMX partial — log girişleri (sayfalama için)."""
    db = _get_db(request)
    category = request.query_params.get("category") or None
    offset = int(request.query_params.get("offset", "0"))

    entries = await db.list_audit_log(category=category, limit=50, offset=offset)

    ctx = {"entries": entries}
    return templates.TemplateResponse(
        request, "partials/log_entries.html", ctx,
    )


# --- Settings ---


# Uygulama başlangıç zamanı (uptime hesabı için)
_APP_START_TIME = datetime.now(UTC)


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request) -> HTMLResponse:
    """Settings sayfası — sistem bilgisi ve bildirim ayarları."""
    db = _get_db(request)

    # Sistem bilgisi
    db_healthy = await db.health_check()
    tags = await db.list_tags()
    instances = await db.list_asset_instances()

    uptime_seconds = (datetime.now(UTC) - _APP_START_TIME).total_seconds()
    uptime_hours = int(uptime_seconds // 3600)
    uptime_minutes = int((uptime_seconds % 3600) // 60)
    uptime_str = f"{uptime_hours}s {uptime_minutes}dk"

    # Son KPI hesaplama zamanı
    last_kpi_time: str = "—"
    kpi_engine_obj = getattr(request.app.state, "kpi_engine", None)
    if kpi_engine_obj is not None:
        last_run = getattr(kpi_engine_obj, "_last_run_at", None)
        if last_run is not None:
            last_kpi_time = last_run.strftime("%H:%M:%S")

    # Anomali model sayısı
    model_count = 0
    detector_obj = getattr(request.app.state, "anomaly_detector", None)
    if detector_obj is not None:
        models = getattr(detector_obj, "_models", {})
        model_count = len(models)

    # Push bildirim durumu
    push_enabled = is_push_enabled()
    subscriptions = await db.list_push_subscriptions()

    ctx = _base_context(
        page_title="Settings",
        active_nav="/dashboard/settings",
        db_healthy=db_healthy,
        tag_count=len(tags),
        asset_count=len(instances),
        uptime=uptime_str,
        last_kpi_time=last_kpi_time,
        model_count=model_count,
        push_enabled=push_enabled,
        subscription_count=len(subscriptions),
    )
    return templates.TemplateResponse(request, "pages/settings.html", ctx)


@router.post("/settings/notifications", response_class=HTMLResponse)
async def update_notification_settings(
    request: Request,
    endpoint: str = Form(...),
    notify_warn: bool = Form(False),
    notify_crit: bool = Form(False),
    quiet_start: str = Form(""),
    quiet_end: str = Form(""),
) -> RedirectResponse:
    """Bildirim ayarlarını günceller (HTMX form)."""
    db = _get_db(request)

    # Sessiz saat parse
    qs: time | None = None
    qe: time | None = None
    if quiet_start:
        parts = quiet_start.split(":")
        qs = time(int(parts[0]), int(parts[1]))
    if quiet_end:
        parts = quiet_end.split(":")
        qe = time(int(parts[0]), int(parts[1]))

    await db.update_push_subscription_settings(
        endpoint=endpoint,
        updates={
            "notify_warn": notify_warn,
            "notify_crit": notify_crit,
            "quiet_start": qs,
            "quiet_end": qe,
        },
    )

    return RedirectResponse(url="/dashboard/settings", status_code=303)


# --- Push API ---


@router.get("/api/push/vapid-public-key")
async def vapid_public_key() -> JSONResponse:
    """Frontend için VAPID public key döndürür."""
    public_key, _ = get_vapid_keys()
    return JSONResponse({"public_key": public_key})


@router.post("/api/push/subscribe")
async def push_subscribe(request: Request) -> JSONResponse:
    """Push subscription kaydeder."""
    db = _get_db(request)
    body = await request.json()

    endpoint = body.get("endpoint", "")
    p256dh = body.get("p256dh", "")
    auth = body.get("auth", "")

    if not endpoint or not p256dh or not auth:
        return JSONResponse(
            {"detail": "endpoint, p256dh ve auth alanları gerekli"},
            status_code=400,
        )

    sub = PushSubscription(endpoint=endpoint, p256dh=p256dh, auth=auth)
    created = await db.upsert_push_subscription(sub)
    return JSONResponse({"id": created.id, "endpoint": created.endpoint})


@router.delete("/api/push/subscribe")
async def push_unsubscribe(request: Request) -> JSONResponse:
    """Push subscription siler."""
    db = _get_db(request)
    body = await request.json()
    endpoint = body.get("endpoint", "")

    if not endpoint:
        return JSONResponse({"detail": "endpoint alanı gerekli"}, status_code=400)

    deleted = await db.delete_push_subscription(endpoint)
    return JSONResponse({"deleted": deleted})


@router.post("/api/push/test")
async def push_test(request: Request) -> JSONResponse:
    """Test bildirimi gönderir."""
    if not is_push_enabled():
        return JSONResponse(
            {"detail": "VAPID anahtarları yapılandırılmamış"},
            status_code=400,
        )

    db = _get_db(request)
    sent = await send_push_notifications(
        db=db,
        title="Custos Test Bildirimi",
        body="Bu bir test bildirimidir. Push bildirimler çalışıyor!",
        severity="warn",
    )
    return JSONResponse({"sent": sent})
