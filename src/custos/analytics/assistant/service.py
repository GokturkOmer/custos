"""Teknik asistan servis singleton'ı (F8b Paket E).

Uygulama başına bir `AssistantRetriever` örneği tutar. İlk çağrıda
bilgi tabanını yükler, embedding modelini (lazy) hazırlar ve FAISS
indeksini kurar. Sonraki çağrılar aynı örneği kullanır — model
yeniden yüklenmez (brief §5.2: "bir kez yüklenir, bellekte kalır").

Mimari not: Singleton modül seviyesindedir; FastAPI `Depends(...)`
ile route'lara enjekte edilir, testlerde `app.dependency_overrides`
üstünden fake retriever verilebilir.
"""

from __future__ import annotations

import threading
from pathlib import Path

import structlog

from custos.analytics.assistant.index import AssistantIndex
from custos.analytics.assistant.loader import load_knowledge_base_multi
from custos.analytics.assistant.retriever import AssistantRetriever
from custos.shared.config import Settings
from custos.shared.config import settings as default_settings

logger = structlog.get_logger(logger_name="assistant.service")

_service: AssistantRetriever | None = None
_lock = threading.Lock()


def get_assistant_retriever() -> AssistantRetriever:
    """Singleton retriever — ilk çağrıda modeli yükleyip indeksi kurar.

    FastAPI `Depends(get_assistant_retriever)` ile route'lara enjekte
    edilir. Testler `app.dependency_overrides` ile bu fonksiyonu
    değiştirerek fake retriever geçirebilir.
    """
    global _service
    if _service is not None:
        return _service
    with _lock:
        if _service is not None:
            return _service
        _service = build_retriever(default_settings)
        return _service


def build_retriever(settings: Settings) -> AssistantRetriever:
    """Verilen ayarlarla bir retriever örneği kurar. Test ve production
    yolları aynı fonksiyonu kullanır; production yalnızca singleton
    sarmalıyor olması fark.

    Bilgi tabanı iki dizinden yüklenir (V11-110 hibrit yapı): repo'daki
    `data/knowledge/` (temel) + `/var/custos/knowledge/local/` (saha-spesifik).
    Aynı slug ikisinde de varsa lokal kazanır (bkz. `load_knowledge_base_multi`).
    """
    knowledge_dirs = [
        Path(settings.custos_assistant_knowledge_dir),
        Path(settings.custos_assistant_knowledge_local_dir),
    ]
    logger.info(
        "assistant_service_init",
        knowledge_dirs=[str(d) for d in knowledge_dirs],
    )
    chunks = load_knowledge_base_multi(knowledge_dirs)
    yaml_chunks = [c for c in chunks if c.yaml_question]
    index = AssistantIndex()
    index.build(chunks)
    return AssistantRetriever(
        index=index,
        yaml_chunks=yaml_chunks,
        score_threshold=settings.custos_assistant_score_threshold,
        top_k=settings.custos_assistant_top_k,
    )


def rebuild_assistant_retriever() -> AssistantRetriever:
    """Singleton'ı sync olarak yeniden kurar (V11-110 indeks reload).

    Settings → Knowledge Base sayfasındaki "İndeksi yeniden oluştur"
    butonu bu fonksiyonu çağırır. Yeni doküman ekleme/silme/düzenleme
    sonrası FAISS indeksini güncellemek için. ~2-3 saniye sürer (24
    doküman, sentence-transformers embedding); pilot ölçeğinde sync
    çağrı yeterli — race-free, basit.
    """
    global _service
    with _lock:
        _service = build_retriever(default_settings)
        return _service


def reset_assistant_retriever() -> None:
    """Test amaçlı — singleton'ı sıfırlar; bir sonraki `get_*` çağrısı
    yeniden kurar."""
    global _service
    with _lock:
        _service = None
