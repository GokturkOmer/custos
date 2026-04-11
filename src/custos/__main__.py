"""Custos ana giriş noktası.

Analytics loop sürecinin FastAPI uygulamasını başlatır.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from custos.analytics.dashboard.app import get_static_files_app, router
from custos.shared.config import settings
from custos.shared.database import DatabaseInterface, create_database

logger = structlog.get_logger(logger_name="app")


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Uygulama yaşam döngüsü — DB bağlantı havuzunu yönetir."""
    db = create_database(settings)
    try:
        await db.connect()
        application.state.db = db
        await logger.ainfo("Uygulama başlatıldı, DB bağlantısı kuruldu")
    except Exception:
        await logger.aerror("DB bağlantısı kurulamadı", exc_info=True)
        application.state.db = None
    yield
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
