"""Retriever unit testleri (F8b Paket D).

FAISS ve sentence-transformer gerçek bağımlılıkları burada kullanılmaz:
`AssistantIndex` fake encoder ile build edilir; tüm daller (exact /
semantic / empty / threshold altı / threshold üstü) izole biçimde
sınanır.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pytest

from custos.analytics.assistant.index import AssistantIndex
from custos.analytics.assistant.loader import Chunk
from custos.analytics.assistant.retriever import (
    EMPTY_MESSAGE,
    AssistantAnswer,
    AssistantRetriever,
    _normalize,
)


def _unit(*xs: float) -> np.ndarray:
    """L2 normalize edilmiş vektör — fake encoder için."""
    arr = np.asarray(xs, dtype=np.float32)
    n = float(np.linalg.norm(arr))
    if n == 0:
        return arr
    return arr / n


def _md_chunk(chunk_id: str, text: str) -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        source_path=f"{chunk_id}.md",
        source_title=text[:30],
        category="sistem",
        text=text,
    )


def _yaml_chunk(chunk_id: str, question: str, answer: str) -> Chunk:
    """YAML chunk loader'ın ürettiği formatı mimler."""
    return Chunk(
        chunk_id=chunk_id,
        source_path=f"{chunk_id}.yaml",
        source_title=f"SSS — {question[:30]}",
        category="ekipman",
        text=f"Soru: {question}\nCevap: {answer}",
        yaml_question=question,
    )


class _FakeEncoder:
    """Metin → sabit vektör eşlemesi. Eşlenmemiş girdi rastgele düşük skor."""

    def __init__(self, mapping: dict[str, np.ndarray], dim: int = 3) -> None:
        self._mapping = mapping
        self._dim = dim

    def __call__(self, texts: Sequence[str]) -> np.ndarray:
        out = np.zeros((len(texts), self._dim), dtype=np.float32)
        for i, t in enumerate(texts):
            # Mapping'te yoksa çok küçük bir sabit (cosine ~0)
            out[i] = self._mapping.get(t, _unit(0.01, 0.01, 0.01))
        return out


def _make_retriever(
    *,
    yaml_chunks: list[Chunk],
    md_chunks: list[Chunk],
    encoder: _FakeEncoder,
    score_threshold: float = 0.35,
    top_k: int = 3,
) -> AssistantRetriever:
    """Retriever + boşlukla build edilmiş index hazırlar."""
    idx = AssistantIndex(encoder=encoder)
    idx.build(yaml_chunks + md_chunks)
    return AssistantRetriever(
        index=idx,
        yaml_chunks=yaml_chunks,
        score_threshold=score_threshold,
        top_k=top_k,
    )


# ---------- normalize ----------


def test_normalize_lowercases_and_strips_punctuation() -> None:
    """Türkçe küçültme + noktalama + fazla boşluk kaldırma."""
    assert _normalize("Chiller NEDIR?") == "chiller nedir"
    # Diakritik stripping sonrası "dünya" → "dunya"
    assert _normalize("   Merhaba,  dünya!  ") == "merhaba dunya"
    # İ / I / i → hepsi i (Türkçe fuzziness)
    assert _normalize("İSTANBUL") == "istanbul"
    assert _normalize("ISTANBUL") == "istanbul"


def test_normalize_turkish_diacritics_stripped() -> None:
    """ç,ş,ğ,ı,ü,ö → c,s,g,i,u,o (operatör diakritiksiz yazabilsin)."""
    assert _normalize("çalışır") == "calisir"
    assert _normalize("Çalışır") == "calisir"
    assert _normalize("şöförün güç düğümü") == "soforun guc dugumu"


def test_normalize_empty_string() -> None:
    """Boş ve sadece noktalama → boş string."""
    assert _normalize("") == ""
    assert _normalize("!!!  ...") == ""


# ---------- exact match ----------


def test_exact_match_returns_yaml_answer() -> None:
    """YAML sorusu normalize edilmiş hali ile sorulunca exact eşleşir."""
    yaml_c = _yaml_chunk("y1", "Chiller nedir?", "Soğutma ekipmanıdır.")
    encoder = _FakeEncoder({yaml_c.text: _unit(1.0, 0.0, 0.0)})
    retriever = _make_retriever(yaml_chunks=[yaml_c], md_chunks=[], encoder=encoder)

    answer = retriever.answer("chiller NEDIR ??")
    assert answer.match_type == "exact"
    assert answer.text == "Soğutma ekipmanıdır."
    assert answer.source_path == "y1.yaml"
    assert answer.score == 1.0


def test_exact_match_preferred_over_semantic() -> None:
    """Hem exact hem semantic hit olsa, exact kazanır."""
    yaml_c = _yaml_chunk("y1", "Pompa nedir?", "Sıvı hareket ettirir.")
    md_c = _md_chunk("m1", "pompa bakim kilavuzu")
    encoder = _FakeEncoder(
        {
            yaml_c.text: _unit(1.0, 0.0, 0.0),
            md_c.text: _unit(0.9, 0.0, 0.0),
            "pompa nedir": _unit(1.0, 0.0, 0.0),  # sorgu embed
        }
    )
    retriever = _make_retriever(
        yaml_chunks=[yaml_c], md_chunks=[md_c], encoder=encoder
    )
    answer = retriever.answer("Pompa nedir?")
    assert answer.match_type == "exact"
    assert answer.source_path == "y1.yaml"


# ---------- semantic ----------


def test_semantic_match_above_threshold() -> None:
    """YAML match yok; semantic skor eşik üstü → semantic answer."""
    md_c = _md_chunk("m1", "chiller alarm basinc yuksek")
    encoder = _FakeEncoder(
        {
            md_c.text: _unit(1.0, 0.0, 0.0),
            "yuksek basinc": _unit(1.0, 0.0, 0.0),
        }
    )
    retriever = _make_retriever(
        yaml_chunks=[], md_chunks=[md_c], encoder=encoder, score_threshold=0.35
    )
    answer = retriever.answer("yuksek basinc")
    assert answer.match_type == "semantic"
    assert answer.source_path == "m1.md"
    assert answer.score is not None
    assert answer.score >= 0.35
    assert answer.text == md_c.text


def test_semantic_below_threshold_returns_empty() -> None:
    """En iyi skor eşik altıysa 'bulamadım' cevabı."""
    md_c = _md_chunk("m1", "chiller bakim")
    encoder = _FakeEncoder(
        {
            md_c.text: _unit(1.0, 0.0, 0.0),
            # Sorgu vektörü neredeyse dik — cosine ~0.1
            "alakasiz sorgu": _unit(0.1, 1.0, 0.0),
        }
    )
    retriever = _make_retriever(
        yaml_chunks=[], md_chunks=[md_c], encoder=encoder, score_threshold=0.35
    )
    answer = retriever.answer("alakasiz sorgu")
    assert answer.match_type == "empty"
    assert answer.text == EMPTY_MESSAGE
    assert answer.source_path is None


def test_semantic_threshold_exact_equal_accepted() -> None:
    """Skor eşiğe eşit ise kabul edilir (>=)."""
    md_c = _md_chunk("m1", "metin")
    encoder = _FakeEncoder(
        {
            md_c.text: _unit(1.0, 0.0, 0.0),
            # 30° açı → cos ≈ 0.866; 0.866 eşikli test yapalım
            "sorgu": _unit(0.866, 0.5, 0.0),
        }
    )
    retriever = _make_retriever(
        yaml_chunks=[], md_chunks=[md_c], encoder=encoder, score_threshold=0.866
    )
    answer = retriever.answer("sorgu")
    assert answer.match_type == "semantic"


# ---------- empty / edge ----------


def test_empty_query_returns_empty_message() -> None:
    """Boş / whitespace sorgu doğrudan empty."""
    encoder = _FakeEncoder({})
    retriever = _make_retriever(yaml_chunks=[], md_chunks=[], encoder=encoder)
    answer = retriever.answer("   ")
    assert answer.match_type == "empty"
    assert answer.text == EMPTY_MESSAGE


def test_empty_knowledge_base_returns_empty_message() -> None:
    """Hiç doküman yok — her sorgu empty döner."""
    encoder = _FakeEncoder({})
    retriever = _make_retriever(yaml_chunks=[], md_chunks=[], encoder=encoder)
    answer = retriever.answer("herhangi bir sorgu")
    assert answer.match_type == "empty"


def test_duplicate_yaml_questions_first_wins() -> None:
    """Aynı normalize anahtarda iki YAML chunk varsa ilk eklenen kazanır."""
    c1 = _yaml_chunk("y1", "Aynı soru", "cevap1")
    c2 = _yaml_chunk("y2", "Aynı  soru!!", "cevap2")
    encoder = _FakeEncoder(
        {c1.text: _unit(1.0, 0.0, 0.0), c2.text: _unit(1.0, 0.0, 0.0)}
    )
    retriever = _make_retriever(yaml_chunks=[c1, c2], md_chunks=[], encoder=encoder)
    answer = retriever.answer("aynı soru")
    assert answer.match_type == "exact"
    assert answer.source_path == "y1.yaml"


def test_assistant_answer_is_frozen() -> None:
    """Accidental mutation engeli."""
    import dataclasses

    ans = AssistantAnswer(text="x", match_type="empty")
    with pytest.raises(dataclasses.FrozenInstanceError):
        ans.text = "y"  # type: ignore[misc]


def test_exact_match_source_title_populated() -> None:
    """Exact dalda source_title chunk'tan gelir."""
    yaml_c = _yaml_chunk("y1", "Nedir?", "Cevap.")
    encoder = _FakeEncoder({yaml_c.text: _unit(1.0, 0.0, 0.0)})
    retriever = _make_retriever(yaml_chunks=[yaml_c], md_chunks=[], encoder=encoder)
    answer = retriever.answer("nedir")
    assert answer.source_title is not None
    assert "SSS" in answer.source_title
