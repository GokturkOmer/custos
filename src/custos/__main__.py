"""Custos ana giriş noktası.

Analytics loop sürecinin FastAPI uygulamasını başlatır.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI

from custos.analytics.anomaly_detector import AnomalyDetector
from custos.analytics.dashboard.app import get_static_files_app, router
from custos.analytics.kpi_engine import KpiEngine
from custos.analytics.threshold_engine import ThresholdEngine
from custos.shared.config import settings
from custos.shared.database import DatabaseInterface, create_database

logger = structlog.get_logger(logger_name="app")

# Anomali model dizini
_MODELS_DIR = Path("data/models")


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
    try:
        await db.connect()
        application.state.db = db
        await logger.ainfo("Uygulama başlatıldı, DB bağlantısı kuruldu")

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
    except Exception:
        await logger.aerror("DB bağlantısı kurulamadı", exc_info=True)
        application.state.db = None
    yield
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

# Dashboard router ve statik dosyaları ekle
app.include_router(router)
app.mount("/static", get_static_files_app(), name="static")
