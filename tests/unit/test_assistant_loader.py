"""Loader + chunker unit testleri (F8b Paket B).

Dosya sistemi tabanlı; `tmp_path` fixture'u ile her test izole dizin
kullanır. DB erişimi yok.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from custos.analytics.assistant.loader import (
    Chunk,
    DocumentSummary,
    load_knowledge_base,
    load_knowledge_base_multi,
    summarize_documents,
)


def _write(path: Path, content: str) -> None:
    """UTF-8 dosya yazım yardımcısı."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_empty_directory_returns_empty_list(tmp_path: Path) -> None:
    """Boş bilgi tabanı — chatbot boş tabanla da çalışmalı."""
    assert load_knowledge_base(tmp_path) == []


def test_nonexistent_directory_returns_empty_list(tmp_path: Path) -> None:
    """Dizin yoksa sessizce boş liste."""
    assert load_knowledge_base(tmp_path / "yok") == []


def test_markdown_with_frontmatter_and_h2_sections(tmp_path: Path) -> None:
    """Frontmatter + 2 `##` başlık → 3 chunk (intro + 2 section)."""
    _write(
        tmp_path / "chiller.md",
        """---
title: "Chiller arıza kılavuzu"
category: ariza
asset_template: chiller
tags: [chiller, alarm]
---

Chiller alarmları bu dokümanda özetlenmiştir.

## Yüksek basınç

Kondenser fanını kontrol et.

## Düşük basınç

Refrigerant seviyesine bak.
""",
    )
    chunks = load_knowledge_base(tmp_path)
    assert len(chunks) == 3
    # İlk chunk: intro
    assert chunks[0].source_title == "Chiller arıza kılavuzu"
    assert "özetlenmiştir" in chunks[0].text
    # İkinci chunk: ilk section
    assert "Yüksek basınç" in chunks[1].source_title
    assert chunks[1].category == "ariza"
    assert chunks[1].asset_template == "chiller"
    assert chunks[1].tags == ("chiller", "alarm")
    # Üçüncü chunk: ikinci section
    assert "Düşük basınç" in chunks[2].source_title


def test_markdown_without_h2_single_chunk(tmp_path: Path) -> None:
    """Hiç `##` yoksa tüm gövde tek chunk olur."""
    _write(
        tmp_path / "giris.md",
        """---
title: "Sistem girişi"
category: sistem
---

Bu doküman Regin PLC hakkındadır.
Sadece genel bilgi içerir.
""",
    )
    chunks = load_knowledge_base(tmp_path)
    assert len(chunks) == 1
    assert chunks[0].source_title == "Sistem girişi"
    assert "Regin PLC" in chunks[0].text
    assert chunks[0].category == "sistem"


def test_markdown_missing_frontmatter_fallback(tmp_path: Path) -> None:
    """Frontmatter yoksa dosya adı başlık, kategori `diger`."""
    _write(
        tmp_path / "notlar.md",
        "## Test başlığı\n\nİçerik satırı.\n",
    )
    chunks = load_knowledge_base(tmp_path)
    assert len(chunks) == 1
    assert chunks[0].source_title.startswith("notlar")
    assert chunks[0].category == "diger"
    assert chunks[0].asset_template is None
    assert chunks[0].tags == ()


def test_markdown_invalid_category_falls_back_to_diger(tmp_path: Path) -> None:
    """Geçersiz kategori fallback'e düşer (loader tolerant)."""
    _write(
        tmp_path / "garip.md",
        """---
title: "Garip"
category: mars_roveri
---

## Alt başlık

metin
""",
    )
    chunks = load_knowledge_base(tmp_path)
    assert len(chunks) == 1
    assert chunks[0].category == "diger"


def test_yaml_qa_each_pair_one_chunk(tmp_path: Path) -> None:
    """YAML Q&A dosyası — her çift bir chunk, `yaml_question` dolu."""
    _write(
        tmp_path / "sss.yaml",
        """title: "Chiller SSS"
category: ekipman
asset_template: chiller
tags: [chiller, sss]
items:
  - q: "Chiller nedir?"
    a: "Soğutma ekipmanıdır."
  - q: "Alarm verirse ne yapmalıyım?"
    a: "Tipini oku, yetkiliye bildir."
""",
    )
    chunks = load_knowledge_base(tmp_path)
    assert len(chunks) == 2
    assert chunks[0].yaml_question == "Chiller nedir?"
    assert "Soru: Chiller nedir?" in chunks[0].text
    assert "Cevap: Soğutma ekipmanıdır." in chunks[0].text
    assert chunks[0].category == "ekipman"
    assert chunks[1].yaml_question == "Alarm verirse ne yapmalıyım?"


def test_yaml_without_items_returns_empty(tmp_path: Path) -> None:
    """`items` yoksa uyarı + 0 chunk (dosya atlanır, hata fırlatılmaz)."""
    _write(
        tmp_path / "bos.yaml",
        """title: "Bos SSS"
category: ekipman
""",
    )
    assert load_knowledge_base(tmp_path) == []


def test_yaml_skips_incomplete_items(tmp_path: Path) -> None:
    """Eksik q veya a içeren item atlanır."""
    _write(
        tmp_path / "yarim.yaml",
        """title: "Yarim SSS"
category: ekipman
items:
  - q: "Tam soru"
    a: "Tam cevap"
  - q: "Cevapsiz soru"
  - a: "Sorusuz cevap"
  - q: ""
    a: "Bos soru"
""",
    )
    chunks = load_knowledge_base(tmp_path)
    assert len(chunks) == 1
    assert chunks[0].yaml_question == "Tam soru"


def test_readme_md_is_skipped(tmp_path: Path) -> None:
    """`README.md` rehber dosyasıdır, indekslenmez."""
    _write(tmp_path / "README.md", "# Rehber\n\n## Bir\n\nİçerik.\n")
    _write(
        tmp_path / "asil.md",
        "---\ntitle: Asil\ncategory: sistem\n---\n\n## Bir\n\nİçerik.\n",
    )
    chunks = load_knowledge_base(tmp_path)
    assert len(chunks) == 1
    assert chunks[0].source_path == "asil.md"


def test_broken_yaml_frontmatter_is_tolerated(tmp_path: Path) -> None:
    """Bozuk frontmatter varsa dosya fallback ile yüklenir."""
    _write(
        tmp_path / "bozuk.md",
        """---
title: "Eksik kapanan
category ariza
---

## Bir

Icerik.
""",
    )
    chunks = load_knowledge_base(tmp_path)
    # Bozuk frontmatter'dan sonra parse'a devam — 1 chunk bekleniyor.
    # Dosya başlığı dosya adından fallback, kategori `diger`.
    assert len(chunks) == 1
    assert chunks[0].category == "diger"


def test_chunk_ids_are_unique(tmp_path: Path) -> None:
    """Üretilen tüm chunk'lar farklı ID'ye sahip."""
    _write(
        tmp_path / "a.md",
        "---\ntitle: A\ncategory: sistem\n---\n\n## X\n\nm.\n\n## Y\n\nn.\n",
    )
    _write(
        tmp_path / "b.yaml",
        "title: B\ncategory: ekipman\nitems:\n  - q: 's'\n    a: 'c'\n",
    )
    chunks = load_knowledge_base(tmp_path)
    ids = [c.chunk_id for c in chunks]
    assert len(ids) == len(set(ids))


def test_nested_directories_are_scanned(tmp_path: Path) -> None:
    """Alt dizinler de taranır."""
    _write(
        tmp_path / "sistem" / "regin.md",
        "---\ntitle: Regin\ncategory: sistem\n---\n\n## Temel\n\nmetin.\n",
    )
    _write(
        tmp_path / "ekipman" / "pompa.yaml",
        "title: Pompa\ncategory: ekipman\nitems:\n  - q: 's'\n    a: 'c'\n",
    )
    chunks = load_knowledge_base(tmp_path)
    assert len(chunks) == 2
    paths = {c.source_path for c in chunks}
    assert "sistem/regin.md" in paths
    assert "ekipman/pompa.yaml" in paths


def test_chunk_is_frozen_dataclass() -> None:
    """`Chunk` frozen olmalı — indeks kurulumu sonrası mutasyon engellenir."""
    chunk = Chunk(
        chunk_id="x",
        source_path="y.md",
        source_title="t",
        category="sistem",
        text="m",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        chunk.text = "farkli"  # type: ignore[misc]


# --- V11-110: hibrit KB (multi-dir + override) ---


def test_multi_dir_local_overrides_git_for_same_slug(tmp_path: Path) -> None:
    """Aynı slug iki dizinde varsa lokal kazanır (V11-110, K6 hibrit)."""
    git_dir = tmp_path / "git"
    local_dir = tmp_path / "local"
    _write(
        git_dir / "chiller.md",
        """---
title: "Chiller (git versiyonu)"
category: ariza
---

## Genel

Genel içerik (git).
""",
    )
    _write(
        local_dir / "chiller.md",
        """---
title: "Chiller (Torunlar saha)"
category: ariza
---

## Saha notu

Torunlar GYO chiller özelleri.
""",
    )
    chunks = load_knowledge_base_multi([git_dir, local_dir])
    titles = [c.source_title for c in chunks]
    # Lokal kazanmalı; git versiyonunun başlığı listede olmamalı.
    assert any("Torunlar saha" in t for t in titles)
    assert not any("git versiyonu" in t for t in titles)


def test_multi_dir_handles_missing_dir_gracefully(tmp_path: Path) -> None:
    """Dizinlerden biri yoksa diğerinden yine de chunk üretilir."""
    git_dir = tmp_path / "git"
    local_dir = tmp_path / "yok_olmayan_dizin"  # exists() == False
    _write(
        git_dir / "ahu.md",
        "---\ntitle: AHU\ncategory: ekipman\n---\n\n## X\n\ny.\n",
    )
    chunks = load_knowledge_base_multi([git_dir, local_dir])
    assert len(chunks) >= 1
    assert any(c.source_title.startswith("AHU") for c in chunks)


def test_multi_dir_combines_unique_slugs_from_both_dirs(tmp_path: Path) -> None:
    """Farklı slug'lar her iki dizinden toplanır (override yok)."""
    git_dir = tmp_path / "git"
    local_dir = tmp_path / "local"
    _write(
        git_dir / "chiller.md",
        "---\ntitle: Chiller\ncategory: ariza\n---\n\n## A\n\nm.\n",
    )
    _write(
        local_dir / "torunlar_pompa.md",
        "---\ntitle: Torunlar Pompa\ncategory: ekipman\n---\n\n## B\n\nn.\n",
    )
    chunks = load_knowledge_base_multi([git_dir, local_dir])
    titles = {c.source_title.split(" — ")[0] for c in chunks}
    assert "Chiller" in titles
    assert "Torunlar Pompa" in titles


# --- V11-110: dashboard listesi için summarize_documents ---


def test_summarize_documents_returns_one_per_file(tmp_path: Path) -> None:
    """`summarize_documents` her dosya için tek özet döner (chunk değil)."""
    _write(
        tmp_path / "chiller.md",
        """---
title: Chiller arıza
category: ariza
---

## A

m.

## B

n.
""",
    )
    _write(
        tmp_path / "sss.yaml",
        (
            "title: SSS\ncategory: ekipman\nitems:\n"
            "  - q: 's1'\n    a: 'c1'\n"
            "  - q: 's2'\n    a: 'c2'\n"
        ),
    )
    summaries = summarize_documents(tmp_path)
    assert len(summaries) == 2
    by_slug = {s.slug: s for s in summaries}
    assert by_slug["chiller"].title == "Chiller arıza"
    assert by_slug["chiller"].file_format == "md"
    assert by_slug["sss"].title == "SSS"
    assert by_slug["sss"].file_format == "yaml"


def test_summarize_documents_skips_readme(tmp_path: Path) -> None:
    """README.md indekslenmediği gibi özet listesinde de görünmemeli."""
    _write(tmp_path / "README.md", "# Rehber\n\nİçerik.\n")
    _write(
        tmp_path / "asil.md",
        "---\ntitle: Asil\ncategory: sistem\n---\n\n## X\n\ny.\n",
    )
    summaries = summarize_documents(tmp_path)
    slugs = [s.slug for s in summaries]
    assert "README" not in slugs and "readme" not in slugs
    assert "asil" in slugs


def test_document_summary_is_frozen() -> None:
    """`DocumentSummary` frozen — UI'a geçtiğinde mutasyon engellenir."""
    summary = DocumentSummary(
        slug="x",
        title="X",
        category="sistem",
        source_path="x.md",
        file_format="md",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        summary.title = "Y"  # type: ignore[misc]
