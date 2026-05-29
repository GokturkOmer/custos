"""Asistan repository entegrasyon testleri — gerçek `assistant` şeması (Faz 1/A).

DB ayakta değilse VEYA migration 038 (`assistant` şeması) uygulanmamışsa testler
atlanır → pytest YEŞİL kalır. Test dokümanları `TEST_` prefix'li (`filename`) ve
fixture başında/sonunda silinir (pilot DB'ye sızıntı yok).

`CUSTOS_TEST_DSN` tanımlıysa o DSN kullanılır (PP-09 deseni); boşsa runtime DSN.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from custos.assistant.repository import AssistantRepository, ChunkInput
from custos.shared.config import Settings

_TEST_FILENAME_PREFIX = "TEST_"


def _test_settings() -> Settings:
    """PP-09: CUSTOS_TEST_DSN tanımlıysa runtime DSN'i onunla override et."""
    s = Settings()
    if s.custos_test_dsn:
        return s.model_copy(update={"custos_db_dsn": s.custos_test_dsn})
    return s


async def _assistant_schema_ready(repository: AssistantRepository) -> bool:
    """`assistant.documents` mevcut mu (migration 038 uygulanmış mı)."""
    pool = repository._get_pool()
    async with pool.acquire() as conn:
        reg = await conn.fetchval("SELECT to_regclass('assistant.documents')::text")
    return reg is not None


async def _cleanup_test_docs(repository: AssistantRepository) -> None:
    """`TEST_` prefix'li dokümanları siler (chunk'lar CASCADE ile gider)."""
    pool = repository._get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM assistant.documents WHERE filename LIKE $1",
            f"{_TEST_FILENAME_PREFIX}%",
        )


async def _count_chunks(repository: AssistantRepository, document_id: int) -> int:
    """Bir dokümanın chunk sayısını döner (repository'de okuma metodu yok)."""
    pool = repository._get_pool()
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM assistant.chunks WHERE document_id = $1",
            document_id,
        )
    return int(count)


@pytest.fixture
def _check_assistant_db() -> None:
    """DB erişilemez veya `assistant` şeması yoksa testi atla."""

    async def _probe() -> bool:
        repository = AssistantRepository(_test_settings())
        try:
            await repository.connect()
            ready = await _assistant_schema_ready(repository)
            await repository.close()
        except Exception:
            return False
        else:
            return ready

    if not asyncio.run(_probe()):
        pytest.skip(
            "assistant şeması erişilemez — DB ayakta + 'alembic upgrade head' (038) gerekli"
        )


@pytest.fixture
async def repo(_check_assistant_db: None) -> AsyncIterator[AssistantRepository]:
    """Bağlı repository; test öncesi/sonrası TEST_ dokümanlarını temizler."""
    repository = AssistantRepository(_test_settings())
    await repository.connect()
    await _cleanup_test_docs(repository)
    try:
        yield repository
    finally:
        await _cleanup_test_docs(repository)
        await repository.close()


async def test_document_lifecycle(repo: AssistantRepository) -> None:
    """insert_document → list_documents → insert_chunks_batch → delete_document."""
    document_id = await repo.insert_document(
        filename="TEST_chiller_manual.pdf",
        equipment_model="30XA-1002",
        equipment_type="chiller",
        language="en",
        total_pages=2,
        ocr_used=False,
        source_pdf_path="/var/lib/custos/assistant/sources/TEST.pdf",
        uploaded_by="test_user",
    )
    assert isinstance(document_id, int)

    # list_documents döndürmeli ve tüm alanlar round-trip etmeli.
    match = [d for d in await repo.list_documents() if d.document_id == document_id]
    assert len(match) == 1
    doc = match[0]
    assert doc.filename == "TEST_chiller_manual.pdf"
    assert doc.equipment_type == "chiller"
    assert doc.equipment_model == "30XA-1002"
    assert doc.language == "en"
    assert doc.total_pages == 2
    assert doc.ocr_used is False
    assert doc.source_pdf_path == "/var/lib/custos/assistant/sources/TEST.pdf"
    assert doc.uploaded_by == "test_user"
    # uploaded_at TIMESTAMPTZ → asyncpg tz-aware datetime döner (UTC, CLAUDE.md).
    assert doc.uploaded_at.tzinfo is not None

    # Sayfa-bazlı chunk'ları toplu ekle (faiss_index_id Faz 1'de NULL).
    await repo.insert_chunks_batch(
        document_id,
        [
            ChunkInput(
                page_no=1,
                text_content="page one text",
                png_path="pages/x/1.png",
                has_figure=True,
            ),
            ChunkInput(
                page_no=2,
                text_content="page two text",
                png_path="pages/x/2.png",
                has_table=True,
            ),
        ],
    )
    assert await _count_chunks(repo, document_id) == 2

    # delete_document → doküman + (CASCADE) chunk'lar gitmeli.
    await repo.delete_document(document_id)
    assert all(d.document_id != document_id for d in await repo.list_documents())
    assert await _count_chunks(repo, document_id) == 0


async def test_insert_chunks_batch_bos_liste_noop(repo: AssistantRepository) -> None:
    """Boş chunk listesi no-op — hata fırlatmamalı."""
    document_id = await repo.insert_document(
        filename="TEST_bos_chunk.pdf",
        equipment_model=None,
        equipment_type="other",
        language=None,
        total_pages=0,
        ocr_used=False,
        source_pdf_path=None,
        uploaded_by=None,
    )
    await repo.insert_chunks_batch(document_id, [])
    assert await _count_chunks(repo, document_id) == 0
