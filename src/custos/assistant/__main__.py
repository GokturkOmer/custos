"""Asistan servisi giriş noktası.

Kullanım: ``python -m custos.assistant``

uvicorn'u ``127.0.0.1:<port>`` üzerinde başlatır (port config'ten, default
8001). Loopback'e bağlanır — dışa açılım Caddy `/assistant/*` reverse proxy
üzerinden (Bölüm 2). Analytics (8000) ve critical süreçlerinden bağımsızdır.
"""

from __future__ import annotations

import uvicorn

from custos.shared.config import settings
from custos.shared.logging import configure_logging


def main() -> None:
    """structlog'u kurar ve asistan FastAPI app'ini uvicorn ile çalıştırır."""
    configure_logging(settings.log_level)
    # App import string yerine nesne olarak verilir (reload yok — pilot servis).
    from custos.assistant.app import app

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=settings.custos_assistant_port,
    )


if __name__ == "__main__":
    main()
