"""Custos ana giriş noktası.

Analytics loop sürecinin FastAPI uygulamasını başlatır.

Watchdog (V11-105/K13):
- systemd Type=notify altında her 30 sn ``WATCHDOG=1`` gönderir.
- Her 30 sn DB'ye ``custos-analytics`` heartbeat'i yazar.
- Her 120 sn cross-service kontrol — ``custos-critical`` 180s'den eski
  ise ``alarm_emergency`` audit log + (gelecekte) push.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from fastapi import FastAPI, Request, Response

from custos.analytics.anomaly_detector import AnomalyDetector
from custos.analytics.archive_scheduler import ArchiveScheduler
from custos.analytics.archiver import ParquetArchiver
from custos.analytics.dashboard.app import _archive_lock, get_static_files_app, router
from custos.analytics.dashboard.auth_routes import auth_router
from custos.analytics.disk_telemetry import DiskMonitor
from custos.analytics.heartbeat import check_heartbeats, write_heartbeat
from custos.analytics.kpi_engine import KpiEngine
from custos.analytics.liveness_engine import LivenessEngine
from custos.analytics.maintenance_mode import expire_check_loop as maintenance_expire_loop
from custos.analytics.maintenance_scheduler import MaintenanceScheduler
from custos.analytics.templates import (
    TemplateLoadError,
    TemplateSchema,
    default_template_dir,
    load_templates,
)
from custos.analytics.threshold_engine import ThresholdEngine
from custos.shared.config import settings
from custos.shared.database import AuditLogEntry, DatabaseInterface, create_database
from custos.shared.watchdog import SystemdWatchdog

logger = structlog.get_logger(logger_name="app")

# Heartbeat aralıkları (V11-105). 30s yazma, 120s cross-check.
HEARTBEAT_WRITE_INTERVAL: float = 30.0
HEARTBEAT_CHECK_INTERVAL: float = 120.0
EXPECTED_SERVICES: tuple[str, ...] = ("custos-analytics", "custos-critical")


async def _heartbeat_writer(db: DatabaseInterface) -> None:
    """Analytics servisinin DB heartbeat'ini periyodik yazar."""
    while True:
        await write_heartbeat(db, "custos-analytics")
        try:
            await asyncio.sleep(HEARTBEAT_WRITE_INTERVAL)
        except asyncio.CancelledError:
            break


async def _heartbeat_cross_check(db: DatabaseInterface) -> None:
    """Cross-service watchdog — diğer servislerin sağlığını izler.

    `custos-critical` 180s'den eski ise audit_log'a `watchdog_stale_service`
    kaydı düşer. Tekrarlayan alarm önlemek için son alarm zamanı izlenir.
    """
    last_alarm_at: dict[str, datetime] = {}
    # Tekrar alarm aralığı — 5 dk (cross-check 120s, 5dk = 2-3 cycle)
    alarm_cooldown_seconds = 300.0

    while True:
        try:
            healths = await check_heartbeats(db, expected_services=list(EXPECTED_SERVICES))
            now = datetime.now(UTC)
            for h in healths:
                if h.state != "down" or h.service_name == "custos-analytics":
                    # Kendi heartbeat'imizi alarm üretmek için kullanmayız
                    # (zaten ölü ise bu task hiç çalışmaz).
                    continue
                last = last_alarm_at.get(h.service_name)
                if last is not None and (now - last).total_seconds() < alarm_cooldown_seconds:
                    continue
                age_str = (
                    f"{h.age_seconds:.0f}s" if h.age_seconds is not None else "hiç"
                )
                await db.insert_audit_log(
                    AuditLogEntry(
                        category="alarm_emergency",
                        action="watchdog_stale_service",
                        entity_type="service",
                        entity_id=h.service_name,
                        detail=(
                            f"Servis cevap vermiyor: {h.service_name} "
                            f"(yaş={age_str}, eşik=180s)"
                        ),
                    ),
                )
                last_alarm_at[h.service_name] = now
                await logger.awarning(
                    "Cross-service watchdog alarmı",
                    service_name=h.service_name,
                    age_seconds=h.age_seconds,
                )
        except Exception:
            await logger.aerror(
                "Cross-service watchdog kontrolünde hata",
                exc_info=True,
            )
        try:
            await asyncio.sleep(HEARTBEAT_CHECK_INTERVAL)
        except asyncio.CancelledError:
            break

# Anomali model dizini
_MODELS_DIR = Path("data/models")

# Parquet arşiv dizini — pilot deploy'da /var/custos/archive, testte override.
_ARCHIVE_DIR = Path("/var/custos/archive")


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Uygulama yaşam döngüsü — DB, threshold engine, KPI engine ve anomaly detector yönetir."""
    db = create_database(settings)
    engine: ThresholdEngine | None = None
    engine_task: asyncio.Task[None] | None = None
    kpi_engine: KpiEngine | None = None
    kpi_task: asyncio.Task[None] | None = None
    detector: AnomalyDetector | None = None
    detector_task: asyncio.Task[None] | None = None
    maint_scheduler: MaintenanceScheduler | None = None
    maint_scheduler_task: asyncio.Task[None] | None = None
    archive_scheduler: ArchiveScheduler | None = None
    archive_scheduler_task: asyncio.Task[None] | None = None
    disk_monitor: DiskMonitor | None = None
    disk_monitor_task: asyncio.Task[None] | None = None
    # Watchdog (V11-105) — sd_notify + cross-service heartbeat task'ları.
    watchdog = SystemdWatchdog(interval_seconds=HEARTBEAT_WRITE_INTERVAL)
    sd_task: asyncio.Task[None] | None = None
    hb_writer_task: asyncio.Task[None] | None = None
    hb_check_task: asyncio.Task[None] | None = None
    # P-04: Bakım modu süre-doldu otomatik kapama loop'u
    maint_expire_task: asyncio.Task[None] | None = None
    # P-05: Stuck-at + counter mode liveness engine
    liveness: LivenessEngine | None = None
    liveness_task: asyncio.Task[None] | None = None
    try:
        await db.connect()
        application.state.db = db
        await logger.ainfo("Uygulama başlatıldı, DB bağlantısı kuruldu")

        # AVM Template Pack — YAML şablonlarını memory'de tut (F9 Paket E).
        # Dashboard template detayında advisory alarm/bakım preview'ı için.
        # Yüklenemezse boş dict (seed yapılmamış olabilir).
        try:
            avm_pack: dict[str, TemplateSchema] = {
                entry.schema.slug: entry.schema for entry in load_templates(default_template_dir())
            }
            application.state.avm_template_pack = avm_pack
            await logger.ainfo(
                "AVM Template Pack yüklendi",
                count=len(avm_pack),
                slugs=sorted(avm_pack.keys()),
            )
        except TemplateLoadError as exc:
            application.state.avm_template_pack = {}
            await logger.awarning(
                "AVM Template Pack yüklenemedi — dashboard preview boş",
                error=str(exc),
            )

        # Threshold engine'i başlat
        engine = ThresholdEngine(db=db)
        engine_task = asyncio.create_task(engine.start())
        application.state.threshold_engine = engine

        # KPI engine'i başlat
        kpi_engine = KpiEngine(db=db)
        kpi_task = asyncio.create_task(kpi_engine.start())
        application.state.kpi_engine = kpi_engine

        # Anomaly detector'ı başlat
        detector = AnomalyDetector(db=db, models_dir=_MODELS_DIR)
        detector_task = asyncio.create_task(detector.start())
        application.state.anomaly_detector = detector

        # Bakım zamanlayıcısını başlat (periyodik bakım + overdue tarama)
        maint_scheduler = MaintenanceScheduler(db=db)
        maint_scheduler_task = asyncio.create_task(maint_scheduler.start())
        application.state.maintenance_scheduler = maint_scheduler

        # Parquet arşiv — manuel endpoint + aylık scheduler (F11 Paket E)
        archiver = ParquetArchiver(db=db, archive_dir=_ARCHIVE_DIR)
        application.state.archiver = archiver
        archive_scheduler = ArchiveScheduler(
            archiver=archiver,
            lock=_archive_lock,
        )
        archive_scheduler_task = asyncio.create_task(archive_scheduler.start())
        application.state.archive_scheduler = archive_scheduler

        # Disk telemetri — 5 dakikada bir doluluk, %85 üstünde push (F11 Paket F)
        disk_monitor = DiskMonitor(db=db, mount_point=str(_ARCHIVE_DIR.parent))
        disk_monitor_task = asyncio.create_task(disk_monitor.start())
        application.state.disk_monitor = disk_monitor

        # Watchdog katman 1+2 — sd_notify + DB heartbeat + cross-check.
        watchdog.notify_ready()
        sd_task = asyncio.create_task(watchdog.heartbeat_loop())
        hb_writer_task = asyncio.create_task(_heartbeat_writer(db))
        hb_check_task = asyncio.create_task(_heartbeat_cross_check(db))
        application.state.watchdog = watchdog

        # P-04 Bakım modu expire check loop — süresi dolan per-instance ve
        # global bakımları her 60 sn otomatik kapatır.
        maint_expire_task = asyncio.create_task(maintenance_expire_loop(db))

        # P-05 Liveness engine — stuck-at + counter mode kural-bazlı
        # sensör donma tespiti. 30 sn tick, bakım modu saygılı.
        liveness = LivenessEngine(db=db)
        liveness_task = asyncio.create_task(liveness.start())
        application.state.liveness_engine = liveness
    except Exception:
        await logger.aerror("DB bağlantısı kurulamadı", exc_info=True)
        application.state.db = None
    yield
    # Watchdog STOPPING + task'ları temizle
    watchdog.notify_stopping()
    for task in (sd_task, hb_writer_task, hb_check_task):
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    # P-04 bakım modu expire loop'unu durdur
    if maint_expire_task is not None and not maint_expire_task.done():
        maint_expire_task.cancel()
        try:
            await maint_expire_task
        except asyncio.CancelledError:
            pass
    # P-05 Liveness engine'i durdur
    if liveness is not None:
        await liveness.stop()
    if liveness_task is not None and not liveness_task.done():
        liveness_task.cancel()
        try:
            await liveness_task
        except asyncio.CancelledError:
            pass
    # Disk monitor'ı durdur
    if disk_monitor is not None:
        await disk_monitor.stop()
    if disk_monitor_task is not None and not disk_monitor_task.done():
        disk_monitor_task.cancel()
        try:
            await disk_monitor_task
        except asyncio.CancelledError:
            pass
    # Archive scheduler'ı durdur
    if archive_scheduler is not None:
        await archive_scheduler.stop()
    if archive_scheduler_task is not None and not archive_scheduler_task.done():
        archive_scheduler_task.cancel()
        try:
            await archive_scheduler_task
        except asyncio.CancelledError:
            pass
    # Bakım zamanlayıcısını durdur
    if maint_scheduler is not None:
        await maint_scheduler.stop()
    if maint_scheduler_task is not None and not maint_scheduler_task.done():
        maint_scheduler_task.cancel()
        try:
            await maint_scheduler_task
        except asyncio.CancelledError:
            pass
    # Anomaly detector'ı durdur
    if detector is not None:
        await detector.stop()
    if detector_task is not None and not detector_task.done():
        detector_task.cancel()
        try:
            await detector_task
        except asyncio.CancelledError:
            pass
    # KPI engine'i durdur
    if kpi_engine is not None:
        await kpi_engine.stop()
    if kpi_task is not None and not kpi_task.done():
        kpi_task.cancel()
        try:
            await kpi_task
        except asyncio.CancelledError:
            pass
    # Threshold engine'i durdur
    if engine is not None:
        await engine.stop()
    if engine_task is not None and not engine_task.done():
        engine_task.cancel()
        try:
            await engine_task
        except asyncio.CancelledError:
            pass
    db_instance: DatabaseInterface | None = getattr(application.state, "db", None)
    if db_instance is not None:
        await db_instance.close()
        await logger.ainfo("DB bağlantısı kapatıldı")


app = FastAPI(
    title="Custos",
    description="Endüstriyel edge izleme sistemi",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)


@app.middleware("http")
async def _inject_global_maintenance(
    request: Request,
    call_next: Any,
) -> Response:
    """P-04: dashboard render'larında base.html üst banner'ı için
    ``request.state.global_maintenance`` doldurur.

    Sadece dashboard sayfaları için DB sorgusu — statik / API / login
    yollarında ekstra latency olmasın diye prefix kontrolü.
    """
    state_obj: dict[str, Any] | None = None
    path = request.url.path
    db_instance: DatabaseInterface | None = getattr(request.app.state, "db", None)
    if db_instance is not None and path.startswith("/dashboard"):
        try:
            cfg = await db_instance.get_retention_config()
            now = datetime.now(UTC)
            until = cfg.global_maintenance_until
            started = cfg.global_maintenance_started_at
            active = started is not None and (until is None or until > now)
            if active:
                state_obj = {
                    "active": True,
                    "until": until,
                    "reason": cfg.global_maintenance_reason,
                    "started_at": started,
                }
        except Exception:
            await logger.awarning(
                "Global maintenance state okunamadı — banner atlandı",
                exc_info=True,
            )
    request.state.global_maintenance = state_obj
    response: Response = await call_next(request)
    return response


# Auth router (login/logout/change-password) — root level, prefix yok
app.include_router(auth_router)
# Dashboard router ve statik dosyaları ekle
app.include_router(router)
app.mount("/static", get_static_files_app(), name="static")
