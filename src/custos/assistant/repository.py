"""Asistan servisi veri erişim katmanı — `assistant` PostgreSQL şeması (karar B).

CLAUDE.md mimari istisnası: asistan AYRI bir süreç olduğu için
`shared/database.py` soyutlamasını KULLANMAZ; kendi asyncpg pool'unu yönetir.
Yine de "ham SQL iş mantığına serpiştirilmez" ilkesi geçerli — TÜM SQL bu
modülde toplanır. (architecture_check `SQL_OUTSIDE_DATABASE` istisnası yalnızca
bu dosyaya açıktır.)

Pool yalnızca `assistant` şemasına bağlanır (`search_path=assistant`); `public`
şemasına HİÇ dokunulmaz (karar A — kullanıcı yetkisi forward_auth + analytics
tarafında). Bağlantı bütçesi: max 10 (3 süreç × 10 ≤ 30, Postgres default 100).

Faz 0 (Bölüm 1) kapsamı: pool yaşam döngüsü (`connect`/`close`) + metot
imzaları. Veri metotlarının gövdeleri Faz 1'de (PDF ingest pipeline) doldurulur;
imzalar kanonik plan §3 ile birebir hizalıdır.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import asyncpg
import structlog

from custos.shared.config import Settings

logger = structlog.get_logger(logger_name="assistant.repository")

# Pool boyutu — karar B bağlantı bütçesi (3 süreç × max 10 ≤ 30).
_POOL_MIN_SIZE = 2
_POOL_MAX_SIZE = 10


@dataclass(frozen=True)
class DocumentRecord:
    """`assistant.documents` satırının okuma modeli (plan §3)."""

    document_id: int
    filename: str
    equipment_model: str | None
    equipment_type: str | None
    language: str | None
    total_pages: int | None
    ocr_used: bool
    source_pdf_path: str | None
    uploaded_at: datetime
    uploaded_by: str | None


@dataclass(frozen=True)
class ChunkRecord:
    """`assistant.chunks` satırının okuma modeli (plan §3)."""

    chunk_id: int
    document_id: int
    page_no: int | None
    text_content: str | None
    png_path: str | None
    section_title: str | None
    faiss_index_id: int | None
    has_table: bool
    has_figure: bool


@dataclass(frozen=True)
class ChunkInput:
    """`insert_chunks_batch` için yazma modeli — `chunk_id` DB tarafından üretilir."""

    page_no: int
    text_content: str
    png_path: str
    section_title: str | None = None
    faiss_index_id: int | None = None
    has_table: bool = False
    has_figure: bool = False


class AssistantRepository:
    """`assistant` şeması data-access katmanı.

    Tek bir asyncpg pool tutar; tüm SQL bu sınıfta toplanır. Kullanım:

        repo = AssistantRepository(settings)
        await repo.connect()
        ...
        await repo.close()
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._pool: asyncpg.Pool[asyncpg.Record] | None = None

    async def connect(self) -> None:
        """asyncpg pool kurar. `search_path=assistant` ile yalnızca kendi şeması.

        DSN runtime rolünden gelir (`database_url_async` → prod'da `custos_app`,
        dev'de tek-user). Pool yalnızca `assistant` şemasını görür; sorgular
        şema-niteliksiz yazılsa bile `public`'e düşmez.
        """
        self._pool = await asyncpg.create_pool(
            dsn=self._settings.database_url_async,
            min_size=_POOL_MIN_SIZE,
            max_size=_POOL_MAX_SIZE,
            server_settings={
                "client_encoding": "UTF8",
                "search_path": "assistant",
            },
        )
        await logger.ainfo(
            "assistant_repository_pool_kuruldu",
            min_size=_POOL_MIN_SIZE,
            max_size=_POOL_MAX_SIZE,
        )

    async def close(self) -> None:
        """Bağlantı havuzunu kapatır (idempotent)."""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
            await logger.ainfo("assistant_repository_pool_kapatildi")

    def _get_pool(self) -> asyncpg.Pool[asyncpg.Record]:
        """Pool'u döndürür; `connect()` çağrılmadıysa hata fırlatır."""
        if self._pool is None:
            msg = "Asistan repository pool kurulmadı — connect() çağrıldı mı?"
            raise RuntimeError(msg)
        return self._pool

    # ------------------------------------------------------------------ #
    # Veri metotları — imzalar kanonik (plan §3). Faz 1 (PDF ingest)
    # metotları aşağıda dolu (insert_document / insert_chunks_batch /
    # list_documents / delete_document). get_chunks_by_faiss_ids / log_query /
    # mark_selected_chunk Faz 2/3'te doldurulacak (şimdilik NotImplementedError).
    # uploaded_at / asked_at DB tarafında TIMESTAMPTZ DEFAULT NOW() (UTC,
    # CLAUDE.md) — Python tarafında naive datetime üretilmez.
    # ------------------------------------------------------------------ #

    async def insert_document(
        self,
        *,
        filename: str,
        equipment_model: str | None,
        equipment_type: str | None,
        language: str | None,
        total_pages: int | None,
        ocr_used: bool,
        source_pdf_path: str | None,
        uploaded_by: str | None,
    ) -> int:
        """Yeni doküman kaydı ekler, `document_id` döner.

        `uploaded_at` DB default (`NOW()`, UTC) ile yazılır — burada zaman
        damgası üretilmez (CLAUDE.md datetime kuralı).
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            document_id = await conn.fetchval(
                """
                INSERT INTO assistant.documents
                    (filename, equipment_model, equipment_type, language,
                     total_pages, ocr_used, source_pdf_path, uploaded_by)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                RETURNING document_id
                """,
                filename,
                equipment_model,
                equipment_type,
                language,
                total_pages,
                ocr_used,
                source_pdf_path,
                uploaded_by,
            )
        await logger.ainfo(
            "assistant_document_eklendi",
            document_id=document_id,
            filename=filename,
            total_pages=total_pages,
        )
        return int(document_id)

    async def insert_chunks_batch(
        self, document_id: int, chunks: list[ChunkInput]
    ) -> None:
        """Bir dokümanın sayfa-bazlı chunk'larını toplu ekler.

        Tek transaction içinde `executemany` — chunk'lar ya tümü ya hiçbiri.
        `faiss_index_id` Faz 1'de NULL kalır (FAISS indeksleme Faz 2). Boş
        liste no-op.
        """
        if not chunks:
            return
        pool = self._get_pool()
        rows = [
            (
                document_id,
                chunk.page_no,
                chunk.text_content,
                chunk.png_path,
                chunk.section_title,
                chunk.faiss_index_id,
                chunk.has_table,
                chunk.has_figure,
            )
            for chunk in chunks
        ]
        async with pool.acquire() as conn, conn.transaction():
            await conn.executemany(
                """
                INSERT INTO assistant.chunks
                    (document_id, page_no, text_content, png_path,
                     section_title, faiss_index_id, has_table, has_figure)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                """,
                rows,
            )
        await logger.ainfo(
            "assistant_chunklar_eklendi",
            document_id=document_id,
            chunk_count=len(chunks),
        )

    async def get_chunks_by_faiss_ids(
        self, faiss_ids: list[int]
    ) -> list[ChunkRecord]:
        """FAISS index id'lerine karşılık gelen chunk'ları döner (retrieval)."""
        raise NotImplementedError("Faz 2 — retrieval'da doldurulacak")

    async def list_documents(self) -> list[DocumentRecord]:
        """Yüklenmiş tüm dokümanların özetini döner (UI listesi), en yeni önce."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT document_id, filename, equipment_model, equipment_type,
                       language, total_pages, ocr_used, source_pdf_path,
                       uploaded_at, uploaded_by
                FROM assistant.documents
                ORDER BY uploaded_at DESC
                """
            )
        return [
            DocumentRecord(
                document_id=row["document_id"],
                filename=row["filename"],
                equipment_model=row["equipment_model"],
                equipment_type=row["equipment_type"],
                language=row["language"],
                total_pages=row["total_pages"],
                ocr_used=row["ocr_used"],
                source_pdf_path=row["source_pdf_path"],
                uploaded_at=row["uploaded_at"],
                uploaded_by=row["uploaded_by"],
            )
            for row in rows
        ]

    async def delete_document(self, document_id: int) -> None:
        """Dokümanı + (CASCADE ile) chunk'larını siler.

        Disk dosya temizliği (PNG'ler / kaynak PDF) repository'nin işi DEĞİL —
        orchestration katmanında ele alınır. Burada yalnız DB satırı düşer;
        `assistant.chunks` FK `ON DELETE CASCADE` ile birlikte gider.
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM assistant.documents WHERE document_id = $1",
                document_id,
            )
        await logger.ainfo("assistant_document_silindi", document_id=document_id)

    async def log_query(
        self,
        *,
        query_text: str,
        result_chunk_ids: list[int],
        query_time_ms: int,
        user_id: int | None,
    ) -> int:
        """Bir sorguyu metrik amaçlı kaydeder, `query_id` döner."""
        raise NotImplementedError("Faz 2 — retrieval'da doldurulacak")

    async def mark_selected_chunk(
        self, query_id: int, selected_chunk_id: int
    ) -> None:
        """Kullanıcının seçtiği sonucu işaretler (UX metriği)."""
        raise NotImplementedError("Faz 3 — görsel UI'da doldurulacak")
