"""Asistan servisi — bağımsız FastAPI uygulaması (karar A/B/D).

Critical ve Analytics'ten BAĞIMSIZ üçüncü süreç (port 8001). Analytics'in
~13 arka plan task'inden (threshold/anomaly/kpi/archive/...) HİÇBİRİ burada
çalışmaz — yalnızca PDF retrieval'a ait minimum yaşam döngüsü.

Yollar literal `/assistant/*` prefix'i altında tanımlanır; Caddy `/assistant/*`
isteğini yol'u KORUYARAK (path-preserving `reverse_proxy`, strip yok) bu servise
geçirir (Bölüm 2). Böylece `/assistant/health` hem Caddy ardında hem doğrudan
8001'de (ve testte) aynı yolda çalışır.

Auth: SIFIR auth kodu (karar A). `X-Custos-User` middleware'i forward_auth
header'ını parse edip `request.state.user`'a yazar; yetkilendirmeyi Caddy +
analytics `require_operator` yapar (Bölüm 2). Tam görsel UI Faz 3; Bölüm 1'de
yalnızca placeholder index + üst bar.
"""

from __future__ import annotations

import html
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from custos.assistant.middleware import CUSTOS_USER_HEADER, parse_custos_user_header
from custos.assistant.repository import AssistantRepository
from custos.shared.config import settings

logger = structlog.get_logger(logger_name="assistant.app")


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Servis yaşam döngüsü — yalnızca `assistant` repository pool'u.

    Analytics'in aksine arka plan motoru/scheduler YOK. Pool kurulamazsa
    (örn. DB henüz ayakta değil / migration uygulanmadı) servis yine ayağa
    kalkar: `/assistant/health` DB'siz 200 döner, pool `None` kalır. Bu sayede
    dev'de Caddy/DB olmadan da servis test edilebilir.
    """
    repository = AssistantRepository(settings)
    application.state.repository = repository
    try:
        await repository.connect()
        await logger.ainfo("Asistan servisi başlatıldı, repository pool kuruldu")
    except Exception:
        await logger.aerror(
            "Asistan repository pool kurulamadı — servis pool'suz devam ediyor",
            exc_info=True,
        )
    yield
    await repository.close()
    await logger.ainfo("Asistan servisi durduruldu")


app = FastAPI(
    title="Custos · Asistan",
    description="Teknik manuel görsel retrieval servisi (LLM'siz, offline)",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)


@app.middleware("http")
async def _parse_custos_user(request: Request, call_next: Any) -> Any:
    """`X-Custos-User` forward_auth header'ını `request.state.user`'a yazar.

    Header yoksa/bozuksa `None` (karar A). Asistanda başka auth kodu yok.
    """
    request.state.user = parse_custos_user_header(request.headers.get(CUSTOS_USER_HEADER))
    return await call_next(request)


@app.get("/assistant/health")
async def health() -> JSONResponse:
    """Sağlık ucu — auth GEREKTİRMEZ, DB'ye bağımlı değil. JSON 200."""
    return JSONResponse({"status": "ok", "service": "custos-assistant"})


@app.get("/assistant", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    """Placeholder ana sayfa — "Custos · Asistan" bar + "← Panele dön" linki.

    Tam görsel arama UI Faz 3'te gelir. Oturum kullanıcısı (forward_auth ile)
    varsa üst barda adı gösterilir; yoksa (dev/Caddy'siz) yalnızca bar.
    """
    user = getattr(request.state, "user", None)
    greeting = f" · {html.escape(user.username)}" if user is not None else ""
    body = f"""<!DOCTYPE html>
<html lang="tr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Custos · Asistan</title>
  <style>
    body {{ margin: 0; font-family: system-ui, sans-serif; background: #0f172a; color: #e2e8f0; }}
    .bar {{ display: flex; align-items: center; gap: 1rem; padding: 0.75rem 1.25rem;
            background: #1e293b; border-bottom: 1px solid #334155; }}
    .bar .title {{ font-weight: 600; }}
    .bar a {{ color: #38bdf8; text-decoration: none; font-size: 0.9rem; }}
    .content {{ padding: 2rem 1.25rem; }}
    .muted {{ color: #94a3b8; }}
  </style>
</head>
<body>
  <div class="bar">
    <a href="/dashboard/overview">← Panele dön</a>
    <span class="title">Custos · Asistan{greeting}</span>
  </div>
  <div class="content">
    <p>Teknik manuel görsel arama servisi.</p>
    <p class="muted">Faz 0 — iskelet. Görsel arama arayüzü Faz 3'te eklenecek.</p>
  </div>
</body>
</html>"""
    return HTMLResponse(body)
