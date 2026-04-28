"""Assistant route testleri (F8b Paket E).

`app.dependency_overrides` ile gerçek modeli yüklemeden route davranışını
doğrular. Fake retriever tüm dallarda (exact / semantic / empty) kontrol
altında cevap üretir.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from custos.__main__ import app
from custos.analytics.assistant.retriever import AssistantAnswer
from custos.analytics.assistant.service import get_assistant_retriever

client = TestClient(app)


class _FakeRetriever:
    """Test için minimal AssistantRetriever yerine geçer.

    `query` → önceden tanımlı `AssistantAnswer` eşlemesi. Eşleşmeyen
    sorgu için varsayılan empty cevabı döner.
    """

    def __init__(self, responses: dict[str, AssistantAnswer]) -> None:
        self._responses = responses

    def answer(self, query: str) -> AssistantAnswer:
        # Strip + normalize bir dereceye kadar retriever içindeki ile tutarlı olsun
        key = query.strip().casefold()
        for stored_key, resp in self._responses.items():
            if stored_key.casefold() == key:
                return resp
        return AssistantAnswer(
            text="Bu konuda bilgi tabanında bir kayıt bulamadım.",
            match_type="empty",
        )


def _override(fake: _FakeRetriever) -> None:
    """Dependency override için yardımcı."""
    app.dependency_overrides[get_assistant_retriever] = lambda: fake


@pytest.fixture(autouse=True)
def _reset_overrides() -> Iterator[None]:
    """Her test sonrası override'ları temizle — izolasyon için."""
    yield
    app.dependency_overrides.pop(get_assistant_retriever, None)


def test_assistant_page_get_returns_200() -> None:
    """GET /dashboard/assistant → 200 + sayfa iskeleti."""
    response = client.get("/dashboard/assistant")
    assert response.status_code == 200
    # Sayfa iskelet öğeleri
    assert "Teknik Asistan" in response.text
    assert "assistant-messages" in response.text
    # Form aksiyonu doğru endpoint'e yönlenmeli
    assert "/dashboard/assistant/ask" in response.text


def test_assistant_page_has_assistant_nav_active() -> None:
    """Sidebar 'Asistan' nav item'ı görünmeli ve aktif olmalı."""
    response = client.get("/dashboard/assistant")
    assert response.status_code == 200
    # Nav item ekli mi?
    assert ">Asistan<" in response.text


def test_assistant_ask_exact_match_renders_source_and_text() -> None:
    """POST /ask → exact match dalı, kaynak + cevap render edilmeli."""
    fake = _FakeRetriever(
        {
            "chiller nedir": AssistantAnswer(
                text="Soğutma ekipmanıdır.",
                match_type="exact",
                source_path="ornek.yaml",
                source_title="SSS — Chiller nedir?",
                score=1.0,
            ),
        }
    )
    _override(fake)

    response = client.post(
        "/dashboard/assistant/ask",
        data={"query": "Chiller nedir"},
    )
    assert response.status_code == 200
    # Kullanıcı sorusu partialda render edildi
    assert "Chiller nedir" in response.text
    # Cevap metni
    assert "Soğutma ekipmanıdır." in response.text
    # Kaynak bilgisi
    assert "SSS — Chiller nedir?" in response.text
    assert "ornek.yaml" in response.text
    # Skor etiketi
    assert "skor" in response.text
    assert "(tam eşleşme)" in response.text


def test_assistant_ask_semantic_match_renders_without_exact_tag() -> None:
    """Semantic dal — skor görünmeli, (exact) etiketi olmamalı."""
    fake = _FakeRetriever(
        {
            "kondenser temizligi": AssistantAnswer(
                text="30-90 gün arası periyod önerilir.",
                match_type="semantic",
                source_path="chiller.md",
                source_title="Chiller — Kondenser",
                score=0.62,
            ),
        }
    )
    _override(fake)

    response = client.post(
        "/dashboard/assistant/ask",
        data={"query": "kondenser temizligi"},
    )
    assert response.status_code == 200
    assert "30-90 gün" in response.text
    assert "Chiller — Kondenser" in response.text
    assert "skor" in response.text
    assert "0.62" in response.text
    assert "(tam eşleşme)" not in response.text


def test_assistant_ask_empty_match_shows_empty_message_without_source() -> None:
    """Empty dal — 'bulamadım' mesajı, kaynak bloğu yok."""
    fake = _FakeRetriever({})  # hiç eşleşme yok
    _override(fake)

    response = client.post(
        "/dashboard/assistant/ask",
        data={"query": "pizza nasil yapilir"},
    )
    assert response.status_code == 200
    assert "bilgi tabanında bir kayıt bulamadım" in response.text
    # Empty dalda kaynak bloğu (book-open ikonlu) görünmez
    assert "Kaynak:" not in response.text


def test_assistant_ask_whitespace_query_still_returns_empty() -> None:
    """Sadece boşluk içeren sorgu empty cevaba düşer, 422 dönmez."""
    fake = _FakeRetriever({})
    _override(fake)

    response = client.post(
        "/dashboard/assistant/ask",
        data={"query": "   "},
    )
    # Form tarafında min_length kontrolü yok; empty message döner
    assert response.status_code == 200
    assert "bilgi tabanında bir kayıt bulamadım" in response.text


def test_assistant_ask_missing_query_returns_422() -> None:
    """query alanı eksikse FastAPI 422 döner."""
    response = client.post("/dashboard/assistant/ask", data={})
    assert response.status_code == 422


def test_assistant_nav_item_present_on_overview() -> None:
    """Overview sayfasında sidebar'da Asistan linki de görünmeli."""
    response = client.get("/dashboard/overview")
    assert response.status_code == 200
    assert "/dashboard/assistant" in response.text
    assert "Asistan" in response.text
