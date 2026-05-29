"""Asistan repository iskelet testleri (Faz 0 / karar B).

DB'ye dokunmaz: pool yaşam döngüsü guard'ı + HENÜZ doldurulmamış (Faz 2/3)
veri metotlarının stub davranışı doğrulanır. Faz 1 metotları (insert_document /
insert_chunks_batch / list_documents / delete_document) artık DB gerektirdiği
için burada DEĞİL, `tests/integration/test_assistant_repository_db.py`'de
gerçek pool ile (migration 038 uygulanmış dev DB) test edilir.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest

from custos.assistant.repository import AssistantRepository
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


# Henüz doldurulmamış (Faz 2/3) veri metotları — NotImplementedError fırlatır.
# Faz 1 metotları (insert_document/insert_chunks_batch/list_documents/
# delete_document) artık dolu; integration testinde doğrulanır.
_STUB_CALLS: list[Callable[[AssistantRepository], Awaitable[object]]] = [
    lambda r: r.get_chunks_by_faiss_ids([1, 2]),
    lambda r: r.log_query(
        query_text="q", result_chunk_ids=[1], query_time_ms=5, user_id=None
    ),
    lambda r: r.mark_selected_chunk(1, 2),
]


@pytest.mark.parametrize("invoke", _STUB_CALLS)
async def test_veri_metotlari_faz23_stub(
    invoke: Callable[[AssistantRepository], Awaitable[object]],
) -> None:
    """Doldurulmamış (Faz 2/3) veri metotları NotImplementedError fırlatır."""
    with pytest.raises(NotImplementedError):
        await invoke(_repo())
