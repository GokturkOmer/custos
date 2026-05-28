"""Asistan repository iskelet testleri (Faz 0 / karar B).

DB'ye dokunmaz: pool yaşam döngüsü guard'ı + veri metotlarının Faz 1 stub
davranışı doğrulanır. Gerçek pool bağlantısı manuel CHECKPOINT'te (migration
038 uygulanmış dev DB) doğrulanır.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest

from custos.assistant.repository import AssistantRepository, ChunkInput
from custos.shared.config import settings


def _repo() -> AssistantRepository:
    return AssistantRepository(settings)


def test_kurulumda_pool_none() -> None:
    """Yeni repository'de pool henüz yok."""
    assert _repo()._pool is None


def test_connect_oncesi_get_pool_hata() -> None:
    """connect() çağrılmadan _get_pool() RuntimeError fırlatır."""
    with pytest.raises(RuntimeError):
        _repo()._get_pool()


# Faz 1/2/3'te doldurulacak veri metotları — şimdilik NotImplementedError.
_STUB_CALLS: list[Callable[[AssistantRepository], Awaitable[object]]] = [
    lambda r: r.insert_document(
        filename="manuel.pdf",
        equipment_model=None,
        equipment_type=None,
        language=None,
        total_pages=None,
        ocr_used=False,
        source_pdf_path=None,
        uploaded_by=None,
    ),
    lambda r: r.insert_chunks_batch(
        1, [ChunkInput(page_no=1, text_content="x", png_path="p.png")]
    ),
    lambda r: r.get_chunks_by_faiss_ids([1, 2]),
    lambda r: r.list_documents(),
    lambda r: r.delete_document(1),
    lambda r: r.log_query(
        query_text="q", result_chunk_ids=[1], query_time_ms=5, user_id=None
    ),
    lambda r: r.mark_selected_chunk(1, 2),
]


@pytest.mark.parametrize("invoke", _STUB_CALLS)
async def test_veri_metotlari_faz1_stub(
    invoke: Callable[[AssistantRepository], Awaitable[object]],
) -> None:
    """Tüm veri metotları Faz 0'da NotImplementedError fırlatır (imza mevcut)."""
    with pytest.raises(NotImplementedError):
        await invoke(_repo())
