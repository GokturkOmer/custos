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
    # Veri metotları — imzalar kanonik (plan §3). Gövdeler Faz 1'de
    # (PDF ingest pipeline) doldurulacak. Şimdilik NotImplementedError.
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
        """Yeni doküman kaydı ekler, `document_id` döner."""
        raise NotImplementedError("Faz 1 — PDF ingest pipeline'da doldurulacak")

    async def insert_chunks_batch(
        self, document_id: int, chunks: list[ChunkInput]
    ) -> None:
        """Bir dokümanın sayfa-bazlı chunk'larını toplu ekler."""
        raise NotImplementedError("Faz 1 — PDF ingest pipeline'da doldurulacak")

    async def get_chunks_by_faiss_ids(
        self, faiss_ids: list[int]
    ) -> list[ChunkRecord]:
        """FAISS index id'lerine karşılık gelen chunk'ları döner (retrieval)."""
        raise NotImplementedError("Faz 2 — retrieval'da doldurulacak")

    async def list_documents(self) -> list[DocumentRecord]:
        """Yüklenmiş tüm dokümanların özetini döner (UI listesi)."""
        raise NotImplementedError("Faz 1 — PDF ingest pipeline'da doldurulacak")

    async def delete_document(self, document_id: int) -> None:
        """Dokümanı + (CASCADE ile) chunk'larını siler."""
        raise NotImplementedError("Faz 1 — PDF ingest pipeline'da doldurulacak")

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
