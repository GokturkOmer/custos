"""FAISS indeksi unit testleri (F8b Paket C).

Gerçek sentence-transformer modeli indirilmez — fake encoder enjekte
edilir. Testler küçük sentetik embedding'lerle index davranışını
doğrular.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pytest

from custos.analytics.assistant.index import AssistantIndex, SearchResult
from custos.analytics.assistant.loader import Chunk


def _unit_vec(*xs: float) -> list[float]:
    """Verilen bileşenleri L2 normalize edilmiş listeye çevirir."""
    arr = np.asarray(xs, dtype=np.float32)
    norm = float(np.linalg.norm(arr))
    if norm == 0:
        return [float(x) for x in arr.tolist()]
    return [float(x) for x in (arr / norm).tolist()]


class _FakeEncoder:
    """Text → sabit eşleme ile fake encoder.

    Testler her chunk metni için deterministik, L2-normalize edilmiş
    vektör döndürür. Eşlenmemiş girdi için küçük bir sabit vektör döner.
    """

    def __init__(self, mapping: dict[str, list[float]], dim: int = 3) -> None:
        self._mapping = mapping
        self._dim = dim

    def __call__(self, texts: Sequence[str]) -> np.ndarray:
        out = np.zeros((len(texts), self._dim), dtype=np.float32)
        for i, t in enumerate(texts):
            vec = self._mapping.get(t, _unit_vec(*[0.01] * self._dim))
            out[i] = np.asarray(vec, dtype=np.float32)
        return out


def _make_chunk(chunk_id: str, text: str) -> Chunk:
    """Kısa Chunk oluşturucu."""
    return Chunk(
        chunk_id=chunk_id,
        source_path=f"{chunk_id}.md",
        source_title=text[:30],
        category="sistem",
        text=text,
    )


def test_empty_index_returns_empty_results() -> None:
    """Build edilmemiş indeks is_empty=True, search=[]"""
    idx = AssistantIndex(encoder=_FakeEncoder({}))
    assert idx.is_empty is True
    assert idx.chunk_count == 0
    assert idx.search("herhangi bir sorgu") == []


def test_build_empty_chunks_keeps_index_empty() -> None:
    """Boş chunk listesi ile build → is_empty=True, exception yok."""
    idx = AssistantIndex(encoder=_FakeEncoder({}))
    idx.build([])
    assert idx.is_empty is True
    assert idx.search("bir sey") == []


def test_build_and_search_top_1_exact() -> None:
    """Sorgu chunk metniyle aynı vektöre map'lense cosine 1.0 dönmeli."""
    c1 = _make_chunk("c1", "chiller alarm basinc")
    c2 = _make_chunk("c2", "pompa titresim")
    encoder = _FakeEncoder(
        {
            "chiller alarm basinc": _unit_vec(1.0, 0.0, 0.0),
            "pompa titresim": _unit_vec(0.0, 1.0, 0.0),
            "chiller alarm": _unit_vec(1.0, 0.0, 0.0),
        }
    )
    idx = AssistantIndex(encoder=encoder)
    idx.build([c1, c2])
    results = idx.search("chiller alarm", top_k=1)
    assert len(results) == 1
    assert results[0].chunk.chunk_id == "c1"
    assert results[0].score == pytest.approx(1.0, abs=1e-5)


def test_search_orders_by_similarity() -> None:
    """3 chunk, sorguya en yakın olan ilk sırada olmalı."""
    c_near = _make_chunk("near", "yakin metin")
    c_mid = _make_chunk("mid", "orta metin")
    c_far = _make_chunk("far", "uzak metin")
    encoder = _FakeEncoder(
        {
            "yakin metin": _unit_vec(1.0, 0.0, 0.0),
            "orta metin": _unit_vec(0.8, 0.6, 0.0),
            "uzak metin": _unit_vec(0.0, 1.0, 0.0),
            "yakin sorgu": _unit_vec(1.0, 0.0, 0.0),
        }
    )
    idx = AssistantIndex(encoder=encoder)
    idx.build([c_far, c_mid, c_near])  # insertion sırası farklı
    results = idx.search("yakin sorgu", top_k=3)
    assert len(results) == 3
    ids = [r.chunk.chunk_id for r in results]
    assert ids == ["near", "mid", "far"]
    assert results[0].score > results[1].score > results[2].score


def test_top_k_larger_than_chunks_is_clamped() -> None:
    """top_k chunk sayısından büyükse chunk sayısına düşer, -1 indeks gelmez."""
    c1 = _make_chunk("c1", "tek chunk")
    encoder = _FakeEncoder(
        {
            "tek chunk": _unit_vec(1.0, 0.0, 0.0),
            "sorgu": _unit_vec(1.0, 0.0, 0.0),
        }
    )
    idx = AssistantIndex(encoder=encoder)
    idx.build([c1])
    results = idx.search("sorgu", top_k=10)
    assert len(results) == 1


def test_empty_query_returns_empty_results() -> None:
    """Boş/whitespace sorgu erken çıkış — embedding çağırılmaz."""
    c1 = _make_chunk("c1", "metin")
    encoder = _FakeEncoder({"metin": _unit_vec(1.0, 0.0, 0.0)})
    idx = AssistantIndex(encoder=encoder)
    idx.build([c1])
    assert idx.search("") == []
    assert idx.search("   \t\n ") == []


def test_rebuild_replaces_previous_index() -> None:
    """Build ikinci kez çağrılırsa eski chunk'lar atılır."""
    c1 = _make_chunk("c1", "ilk metin")
    c2 = _make_chunk("c2", "ikinci metin")
    encoder = _FakeEncoder(
        {
            "ilk metin": _unit_vec(1.0, 0.0, 0.0),
            "ikinci metin": _unit_vec(0.0, 1.0, 0.0),
            "ilk": _unit_vec(1.0, 0.0, 0.0),
            "ikinci": _unit_vec(0.0, 1.0, 0.0),
        }
    )
    idx = AssistantIndex(encoder=encoder)
    idx.build([c1])
    idx.build([c2])
    assert idx.chunk_count == 1
    results = idx.search("ikinci", top_k=1)
    assert len(results) == 1
    assert results[0].chunk.chunk_id == "c2"


def test_search_result_is_frozen() -> None:
    """SearchResult frozen — accidental mutation engellenmiş."""
    c = _make_chunk("x", "m")
    result = SearchResult(chunk=c, score=0.5)
    import dataclasses

    with pytest.raises(dataclasses.FrozenInstanceError):
        result.score = 0.9  # type: ignore[misc]


def test_encoder_wrong_dimension_raises() -> None:
    """Sorgu encoder'ı farklı boyutta vektör dönerse ValueError."""
    c1 = _make_chunk("c1", "metin")

    # Build sırasında 3-dim döner; search sırasında 5-dim.
    class _VariableDim:
        def __init__(self) -> None:
            self.count = 0

        def __call__(self, texts: Sequence[str]) -> np.ndarray:
            self.count += 1
            dim = 3 if self.count == 1 else 5
            return np.asarray(
                [[1.0] + [0.0] * (dim - 1) for _ in texts],
                dtype=np.float32,
            )

    encoder = _VariableDim()
    idx = AssistantIndex(encoder=encoder)
    idx.build([c1])
    with pytest.raises(ValueError):
        idx.search("sorgu", top_k=1)


def test_encoder_mismatched_batch_size_raises() -> None:
    """Build sırasında encoder chunk sayısından farklı satır dönerse hata."""

    def bad_encoder(texts: Sequence[str]) -> np.ndarray:
        # Her zaman tek satır — build 2 chunk verse bile.
        return np.asarray([[1.0, 0.0, 0.0]], dtype=np.float32)

    c1 = _make_chunk("c1", "a")
    c2 = _make_chunk("c2", "b")
    idx = AssistantIndex(encoder=bad_encoder)
    with pytest.raises(ValueError):
        idx.build([c1, c2])


def test_chunk_count_reflects_build() -> None:
    """chunk_count build sonrası doğru sayı döner."""
    chunks = [_make_chunk(f"c{i}", f"metin {i}") for i in range(5)]
    encoder = _FakeEncoder(
        {c.text: _unit_vec(float(i + 1), 0.0, 0.0) for i, c in enumerate(chunks)}
    )
    idx = AssistantIndex(encoder=encoder)
    idx.build(chunks)
    assert idx.chunk_count == 5
    assert idx.is_empty is False
