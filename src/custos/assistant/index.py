"""FAISS tabanlı semantic search indeksi (F8b Paket C).

Chunk listesi + encoder callable → FAISS `IndexFlatIP` (cosine benzerlik).

Tercih: `IndexFlatIP` (Inner Product). Encoder vektörleri L2 normalize
ettiği için IP sonucu cosine'e eşit. Pilotta ~200 doküman / ~500 chunk
beklendiği için approximate indekslere (IVF/HNSW) gerek yok — flat
brute-force milisaniye altı cevap verir.

Persist v1.1'e bırakıldı (kullanıcı onayı); uygulama başlangıcında
rebuild yapılır. ~500 chunk × 384 dim embedding < 1 saniyede üretilir.

Test edilebilirlik: `AssistantIndex(encoder=fake)` ile encoder enjekte
edilebilir. Gerçek model indirme testlerde gerekmiyor.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import structlog

from custos.assistant.embeddings import Encoder, get_default_encoder
from custos.assistant.loader import Chunk

logger = structlog.get_logger(logger_name="assistant.index")


@dataclass(frozen=True)
class SearchResult:
    """Tek bir arama sonucu — chunk + cosine benzerlik skoru.

    Skor [-1, 1] aralığında; L2-normalize edilmiş embedding + IP için
    cosine ile aynıdır. Pratikte pozitif aralık (0.0 - 1.0) kullanılır;
    çok alakasız sorgu 0'a yakın, exact eşleşme 1'e yakın döner.
    """

    chunk: Chunk
    score: float


class AssistantIndex:
    """Chunk koleksiyonu üstüne kurulan FAISS arama indeksi.

    Kullanım:
        >>> index = AssistantIndex(encoder=get_default_encoder())
        >>> index.build(chunks)
        >>> results = index.search("chiller alarm", top_k=3)
    """

    def __init__(self, encoder: Encoder | None = None) -> None:
        # Varsayılan: brief'te kilitli multilingual model.
        self._encoder: Encoder = encoder if encoder is not None else get_default_encoder()
        self._chunks: list[Chunk] = []
        self._faiss_index: object | None = None  # faiss.Index — lazy import
        self._dim: int | None = None

    @property
    def chunk_count(self) -> int:
        """İndekslenmiş chunk sayısı."""
        return len(self._chunks)

    @property
    def is_empty(self) -> bool:
        """Boş indeks (kurulmamış veya 0 chunk) — arama boş döner."""
        return self._faiss_index is None or self._dim is None or not self._chunks

    def build(self, chunks: list[Chunk]) -> None:
        """Chunk listesini encode edip FAISS indeksini kurar.

        Tekrar çağrılırsa eski indeks atılır (v1.1'de doküman ekleme
        sonrası re-index için). Boş liste gelirse indeks boş kalır.
        """
        self._chunks = list(chunks)
        if not self._chunks:
            self._faiss_index = None
            self._dim = None
            logger.info("index_built_empty")
            return

        texts = [c.text for c in self._chunks]
        embeddings = self._encoder(texts)

        if embeddings.ndim != 2 or embeddings.shape[0] != len(self._chunks):
            raise ValueError(
                f"Encoder çıkış shape uyumsuz: beklenen "
                f"({len(self._chunks)}, D), gelen {embeddings.shape}"
            )

        # faiss import'u burada: test ortamında modül yüklemesi pahalı olmasın.
        import faiss

        self._dim = int(embeddings.shape[1])
        # IndexFlatIP: normalize edilmiş vektörler için cosine benzerliği verir.
        faiss_index = faiss.IndexFlatIP(self._dim)
        # faiss C++ katmanı float32 bekler; _ensure_float32 dtype güvencesi.
        faiss_index.add(_ensure_float32(embeddings))
        self._faiss_index = faiss_index
        logger.info(
            "index_built",
            chunk_count=len(self._chunks),
            dim=self._dim,
        )

    def search(self, query: str, top_k: int = 3) -> list[SearchResult]:
        """Sorguyu encode edip en yakın `top_k` chunk'ı döner.

        İndeks boşsa boş liste. `top_k` chunk sayısından büyükse
        otomatik olarak chunk sayısına kısaltılır (FAISS aksi halde
        -1'lerle doldurur).
        """
        if self.is_empty:
            return []
        assert self._faiss_index is not None  # is_empty kontrolü
        assert self._dim is not None

        query_text = query.strip()
        if not query_text:
            return []

        effective_k = min(top_k, len(self._chunks))
        if effective_k <= 0:
            return []

        query_embedding = self._encoder([query_text])
        if query_embedding.ndim != 2 or query_embedding.shape[1] != self._dim:
            raise ValueError(
                f"Sorgu embedding shape uyumsuz: beklenen (1, {self._dim}), "
                f"gelen {query_embedding.shape}"
            )

        scores, indices = self._faiss_index.search(  # type: ignore[attr-defined]
            _ensure_float32(query_embedding), effective_k
        )
        # scores / indices: shape (1, effective_k)
        results: list[SearchResult] = []
        for score, idx in zip(scores[0].tolist(), indices[0].tolist(), strict=True):
            if idx < 0 or idx >= len(self._chunks):
                continue
            results.append(
                SearchResult(
                    chunk=self._chunks[idx],
                    score=float(score),
                )
            )
        return results


def _ensure_float32(arr: np.ndarray) -> np.ndarray:
    """FAISS C++ katmanının beklediği `float32` contiguous ndarray'i sağlar."""
    if arr.dtype != np.float32:
        arr = arr.astype(np.float32)
    if not arr.flags["C_CONTIGUOUS"]:
        arr = np.ascontiguousarray(arr)
    return arr
