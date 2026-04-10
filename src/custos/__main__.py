"""Custos ana giriş noktası.

Analytics loop sürecinin FastAPI uygulamasını başlatır.
"""

from __future__ import annotations

from fastapi import FastAPI

from custos.analytics.dashboard.app import get_static_files_app, router

app = FastAPI(
    title="Custos",
    description="Endüstriyel edge izleme sistemi",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
)

# Dashboard router ve statik dosyaları ekle
app.include_router(router)
app.mount("/static", get_static_files_app(), name="static")
