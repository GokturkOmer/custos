"""Loglama altyapısı.

structlog tabanlı yapılandırılmış loglama. Terminal varsa renkli
konsol çıktısı, yoksa JSON formatı kullanır.
"""

from __future__ import annotations

import sys
from typing import Any

import structlog


def configure_logging(level: str = "INFO") -> None:
    """Loglama altyapısını yapılandırır.

    Args:
        level: Log seviyesi (DEBUG, INFO, WARNING, ERROR, CRITICAL).
    """
    # Terminal mi yoksa pipe/dosya mı?
    is_tty = sys.stderr.isatty()

    if is_tty:
        # Geliştirme: okunabilir renkli konsol çıktısı
        renderer: structlog.types.Processor = structlog.dev.ConsoleRenderer()
    else:
        # Üretim: JSON formatı
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.TimeStamper(fmt="iso"),
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            structlog.processors.NAME_TO_LEVEL[level.lower()]
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> Any:
    """İsimlendirilmiş bir logger döndürür.

    Args:
        name: Logger adı (genellikle modül adı).

    Returns:
        Yapılandırılmış structlog logger instance'ı.
    """
    return structlog.get_logger(logger_name=name)
