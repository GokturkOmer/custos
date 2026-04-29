"""Asistan bilgi tabanı smoke test (P-08 tamamlanma kriteri).

`tests/assistant_smoke_questions.yaml` içindeki 10 soruyu gerçek bilgi
tabanına (`data/knowledge/`) sorar. Pass kriteri: 10 sorudan ≥ 8 tanesi
"doğru chunk" döndürmeli — yani retriever empty cevap vermemeli ve
top chunk'ın source_path stem'i sorunun `acceptable_slugs` listesinde
bulunmalı.

Test gerçek sentence-transformer modelini ilk kez yüklerken indirir
(~250 MB, brief §5.1'de kilitli `paraphrase-multilingual-MiniLM-L12-v2`).
İndirme veya disk alanı uygun değilse `CUSTOS_SKIP_KB_SMOKE_TEST=1` ile
atlanır.

Yavaş ama nadir koşulan integration testidir; CI'da gerekirse env var
ile devre dışı bırakılabilir. Pilot kabul kriteri (brief v1.7 §2.1)
"chatbot ≥1 doğru cevap verir" — bu testin geçmesi karşılığıdır.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

# Bilgi tabanı boş veya knowledge dizini yoksa (örn. fresh checkout) test
# anlamsız — atla. Aynı şekilde modelin indirilmesi engellenmişse atla.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_KB_DIR = _REPO_ROOT / "data" / "knowledge"
_SMOKE_QUESTIONS_PATH = _REPO_ROOT / "tests" / "assistant_smoke_questions.yaml"

pytestmark = pytest.mark.skipif(
    os.environ.get("CUSTOS_SKIP_KB_SMOKE_TEST") == "1",
    reason="CUSTOS_SKIP_KB_SMOKE_TEST=1 set — model indirme atlanıyor.",
)


def _load_smoke_questions() -> list[dict[str, object]]:
    """Smoke YAML dosyasından soru listesini okur."""
    raw = _SMOKE_QUESTIONS_PATH.read_text(encoding="utf-8")
    data = yaml.safe_load(raw) or {}
    questions = data.get("questions")
    if not isinstance(questions, list):
        raise ValueError(
            f"{_SMOKE_QUESTIONS_PATH} bekleneni içermiyor: 'questions' liste."
        )
    return questions


def test_kb_directory_has_minimum_documents() -> None:
    """KB dizini en az 20 .md ve 5 .yaml dosyası içermeli (P-08 kriter)."""
    md_count = len(list(_KB_DIR.glob("*.md"))) - 1  # README.md hariç
    yaml_count = len(list(_KB_DIR.glob("*.yaml")))
    assert md_count >= 20, f"En az 20 .md beklenir, bulundu: {md_count}"
    assert yaml_count >= 5, f"En az 5 .yaml beklenir, bulundu: {yaml_count}"


def test_smoke_questions_yaml_well_formed() -> None:
    """Smoke YAML 10 soru içerir, her sorunun gerekli alanları var."""
    questions = _load_smoke_questions()
    assert len(questions) == 10, f"10 soru bekleniyor, bulundu: {len(questions)}"
    for idx, q in enumerate(questions):
        assert "soru" in q, f"#{idx}: 'soru' alanı eksik"
        assert "acceptable_slugs" in q, f"#{idx}: 'acceptable_slugs' alanı eksik"
        assert isinstance(q["acceptable_slugs"], list) and q["acceptable_slugs"]


def test_assistant_smoke_passes_eight_of_ten() -> None:
    """Asistan 10 sorudan en az 8'inde doğru chunk dönmeli.

    Ağır test: model indirme + indeks kurulumu (~30 saniye ilk çalıştırmada).
    `CUSTOS_SKIP_KB_SMOKE_TEST=1` ile atlanabilir.
    """
    # Lazy import — model + faiss bağımlılığı pytest collection sırasında
    # yüklenmesin. Hızlı testler için tipik fixture etkisi.
    from custos.analytics.assistant.service import build_retriever
    from custos.shared.config import settings as default_settings

    questions = _load_smoke_questions()
    retriever = build_retriever(default_settings)

    passed = 0
    failures: list[str] = []
    for q in questions:
        soru = str(q["soru"])
        slugs_raw = q["acceptable_slugs"]
        assert isinstance(slugs_raw, list)
        acceptable_slugs = [str(s) for s in slugs_raw]
        answer = retriever.answer(soru)
        if answer.match_type == "empty":
            failures.append(f"BOŞ: {soru!r}")
            continue
        if answer.source_path is None:
            failures.append(f"source_path None: {soru!r}")
            continue
        slug = Path(answer.source_path).stem
        if slug in acceptable_slugs:
            passed += 1
        else:
            failures.append(
                f"slug uyumsuz: {soru!r} → {slug!r} "
                f"(beklenen biri: {acceptable_slugs!r}, "
                f"match_type={answer.match_type}, score={answer.score})"
            )

    # Detaylı tanı için pytest çıktısına başarısız soruları yaz.
    if failures:
        print("\n=== Smoke test başarısızlıkları ===")  # noqa: T201
        for f in failures:
            print(f" - {f}")  # noqa: T201

    assert passed >= 8, (
        f"En az 8/10 başarı bekleniyor; sonuç {passed}/10. "
        f"Detay yukarıdaki çıktıda."
    )
