"""Retrieval orchestrator — YAML exact-match + semantic search (F8b Paket D).

Akış (brief v1.5 §4.9 + v1.7 §8 risk azaltımı):
1. Sorgu normalize edilir (küçük harf, unicode, noktalama).
2. YAML exact-match: herhangi bir YAML chunk'ın normalize edilmiş
   `yaml_question` alanı sorguyla eşleşirse doğrudan o chunk döner.
   Türkçe semantic search kalitesi düşük kalma riski bu fallback ile
   absorbe edilir (brief §8 "Sentence-transformers Türkçe kalitesi").
3. Semantic search: FAISS ile top-K; en yüksek skor `score_threshold`
   üstüyse o chunk döner.
4. Hiçbiri değilse: `AssistantAnswer(match_type="empty", ...)` —
   "Bu konuda bilgi tabanında bir kayıt bulamadım." mesajı.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

import structlog

from custos.assistant.index import AssistantIndex
from custos.assistant.loader import Chunk

logger = structlog.get_logger(logger_name="assistant.retriever")


MatchType = Literal["exact", "semantic", "empty"]


@dataclass(frozen=True)
class AssistantAnswer:
    """Retriever'ın kullanıcıya sunulmak üzere ürettiği tek atımlık cevap.

    - `match_type="exact"` : YAML Q&A exact-match, `text` cevaptır.
    - `match_type="semantic"` : FAISS top hit, `text` chunk gövdesi.
    - `match_type="empty"` : Kayıt bulunamadı; `text` kibar ret mesajı,
      `source_path` / `source_title` None.
    """

    text: str
    match_type: MatchType
    source_path: str | None = None
    source_title: str | None = None
    score: float | None = None


EMPTY_MESSAGE = "Bu konuda bilgi tabanında bir kayıt bulamadım."

# Türkçe karakterleri ASCII muadili ile eşler — exact-match normalize'da
# operatörün diakritiksiz yazmasına müsamaha için. `str.translate` basit
# ve hızlıdır; Unicode NFKD yaklaşımı `ı` gibi prekompoze harfleri kaçırır.
_TR_ASCII_MAP = str.maketrans(
    {
        "ı": "i",
        "İ": "i",
        "ş": "s",
        "Ş": "s",
        "ğ": "g",
        "Ğ": "g",
        "ü": "u",
        "Ü": "u",
        "ö": "o",
        "Ö": "o",
        "ç": "c",
        "Ç": "c",
    }
)


class AssistantRetriever:
    """Bilgi tabanı arama orchestrator'ı.

    Kullanım:
        >>> retriever = AssistantRetriever(
        ...     index=index,
        ...     yaml_chunks=[c for c in chunks if c.yaml_question],
        ...     score_threshold=0.60,
        ...     top_k=3,
        ... )
        >>> answer = retriever.answer("Chiller nedir?")
    """

    def __init__(
        self,
        *,
        index: AssistantIndex,
        yaml_chunks: list[Chunk],
        score_threshold: float,
        top_k: int,
    ) -> None:
        self._index = index
        self._score_threshold = score_threshold
        self._top_k = top_k
        # Normalize edilmiş soru → chunk eşlemesi. Aynı normalize sonucu
        # farklı chunk'lara eşleşirse ilk kazananı korur (pilot için yeterli).
        self._exact_lookup: dict[str, Chunk] = {}
        for chunk in yaml_chunks:
            if not chunk.yaml_question:
                continue
            key = _normalize(chunk.yaml_question)
            if not key or key in self._exact_lookup:
                continue
            self._exact_lookup[key] = chunk

    def answer(self, query: str) -> AssistantAnswer:
        """Kullanıcı sorusunu cevaba çevirir."""
        stripped = query.strip()
        if not stripped:
            return AssistantAnswer(text=EMPTY_MESSAGE, match_type="empty")

        # 1) YAML exact-match
        normalized = _normalize(stripped)
        exact_chunk = self._exact_lookup.get(normalized)
        if exact_chunk is not None:
            answer_text = _extract_yaml_answer(exact_chunk)
            logger.info(
                "assistant_answer_exact",
                chunk_id=exact_chunk.chunk_id,
            )
            return AssistantAnswer(
                text=answer_text,
                match_type="exact",
                source_path=exact_chunk.source_path,
                source_title=exact_chunk.source_title,
                score=1.0,
            )

        # 2) Semantic search
        results = self._index.search(stripped, top_k=self._top_k)
        if results and results[0].score >= self._score_threshold:
            top = results[0]
            logger.info(
                "assistant_answer_semantic",
                chunk_id=top.chunk.chunk_id,
                score=top.score,
            )
            return AssistantAnswer(
                text=top.chunk.text,
                match_type="semantic",
                source_path=top.chunk.source_path,
                source_title=top.chunk.source_title,
                score=top.score,
            )

        # 3) Boş — eşik altı veya hiç sonuç yok
        logger.info(
            "assistant_answer_empty",
            top_score=(results[0].score if results else None),
        )
        return AssistantAnswer(text=EMPTY_MESSAGE, match_type="empty")


def _normalize(text: str) -> str:
    """YAML exact-match için metin normalizasyonu.

    Adımlar:
    1. Türkçe diakritik → ASCII eşleme (ç→c, ş→s, ğ→g, ı/İ→i, ü→u, ö→o).
       Amaç: operatör diakritiksiz yazabilir ("calisir" = "çalışır"),
       klavye varyasyonlarında I/İ/ı hepsi `i`'ye düşer (brief §8
       Türkçe kalite riski azaltımına katkı).
    2. `casefold()` — Unicode-aware küçük harfe çevirme.
    3. Noktalama → boşluk (Türkçe harfler artık ASCII; `\\w` korur).
    4. Ardışık boşlukları tek boşluğa indirgeme + strip.

    Not: Bu fuzziness kasıtlıdır — exact-match soruyla birebir eşleşmeyi
    beklemek yerine makul varyasyonları kabul eder. Semantic search yolu
    zaten farklı bir recall profili sağlıyor.
    """
    transliterated = text.translate(_TR_ASCII_MAP)
    lowered = transliterated.casefold()
    cleaned = re.sub(r"[^\w\s]", " ", lowered, flags=re.UNICODE)
    collapsed = re.sub(r"\s+", " ", cleaned).strip()
    return collapsed


def _extract_yaml_answer(chunk: Chunk) -> str:
    """YAML chunk'ın `"Soru: ...\\nCevap: ..."` metninden cevap kısmını çeker.

    Parse edemezsek tüm metni döneriz — güvenli fallback.
    """
    lines = chunk.text.split("\n", 1)
    if len(lines) == 2 and lines[1].startswith("Cevap: "):
        return lines[1][len("Cevap: ") :].strip()
    # Fallback: başlık varsa onu dışarıda bırakmadan ham text
    return chunk.text
