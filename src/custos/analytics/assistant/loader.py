"""Bilgi tabanı loader ve chunker (F8b Paket B).

`data/knowledge/` altındaki Markdown ve YAML dosyalarını okur, her
dokümanı arama indeksine uygun küçük parçalara (chunk) böler.

Chunk stratejisi (brief v1.5 §4.9):
- Markdown: her `##` başlığı + altındaki metin bir chunk olur.
  İlk `##` öncesindeki girizgah metni (varsa) ayrı bir chunk
  (başlık = doküman başlığı) olarak eklenir. Dokümanın tamamı
  başlıksızsa tek chunk döner.
- YAML: dosya `items: [{q, a}]` listesi içerir. Her `(q, a)` çifti
  bir chunk'tır; chunk metni `"Soru: ...\nCevap: ..."` formatındadır.

Frontmatter (her iki formatta da): `title`, `category`
(sistem/ekipman/ariza/bakim), opsiyonel `asset_template`, `tags`.
Markdown'da YAML frontmatter `---` blokları ile, YAML dosyasında
dosya kökünde anahtar olarak verilir.

Loader kasıtlı olarak tolerant: frontmatter eksikse dosya adı
başlık olarak, `diger` kategorisi fallback olarak kullanılır.
Doküman kalitesini zorlamak loader'ın işi değil (brief: "doküman
kalitesi = cevap kalitesi, kontrol geliştiricide").
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import structlog
import yaml

logger = structlog.get_logger(logger_name="assistant.loader")

# Markdown frontmatter ayraç blok deseni: dosyanın başında üç çizgi ile
# sarılmış YAML bloğu.
_FRONTMATTER_RE = re.compile(
    r"\A---\r?\n(?P<body>.*?)\r?\n---\r?\n(?P<rest>.*)\Z",
    re.DOTALL,
)

# `## ...` tarzı seviye-2 başlık — satır başında, sadece iki `#` ve boşluk.
# `###` seviye 2 olarak sayılmaz (daha derin başlıklar üst chunk içinde kalır).
_H2_RE = re.compile(r"^##[ \t]+(?P<title>.+?)\s*$", re.MULTILINE)

_VALID_CATEGORIES = frozenset({"sistem", "ekipman", "ariza", "bakim", "diger"})


@dataclass(frozen=True)
class Chunk:
    """Arama indeksine giren en küçük parça.

    `text` embedding'e verilen metindir; `source_path` kaynak dosya
    yolu (repo köküne göre), `source_title` kullanıcıya gösterilecek
    kısa etiket (frontmatter title + opsiyonel section başlığı).
    """

    chunk_id: str
    source_path: str
    source_title: str
    category: str
    text: str
    asset_template: str | None = None
    tags: tuple[str, ...] = field(default_factory=tuple)
    # YAML Q&A chunk'ları için ham soru — exact-match fallback bunu kullanır.
    yaml_question: str | None = None


@dataclass(frozen=True)
class _DocMeta:
    """Frontmatter'dan çıkarılan doküman metadata'sı."""

    title: str
    category: str
    asset_template: str | None
    tags: tuple[str, ...]


def load_knowledge_base(knowledge_dir: Path) -> list[Chunk]:
    """Verilen dizindeki tüm `*.md` ve `*.yaml/*.yml` dosyalarını okuyup
    chunk listesi üretir.

    Dizin yoksa veya boşsa boş liste döner (brief: "chatbot doküman
    olmadan da çalışır"). Bozuk frontmatter veya parse hatası tek bir
    dosyayı atlar, diğerlerini etkilemez — structlog ile warning bırakır.
    """
    if not knowledge_dir.exists() or not knowledge_dir.is_dir():
        logger.info(
            "knowledge_dir_not_found",
            path=str(knowledge_dir),
        )
        return []

    chunks: list[Chunk] = []
    md_files = sorted(knowledge_dir.rglob("*.md"))
    yaml_files = sorted(
        [*knowledge_dir.rglob("*.yaml"), *knowledge_dir.rglob("*.yml")]
    )

    for path in md_files:
        # README.md bilgi tabanı için rehber — indeksleme dışı bırak.
        if path.name.lower() == "readme.md":
            continue
        try:
            chunks.extend(_load_markdown(path, knowledge_dir))
        except Exception as exc:  # noqa: BLE001 — tek dosya hatası diğerini kesmesin
            logger.warning(
                "markdown_load_failed",
                path=str(path),
                error=str(exc),
            )

    for path in yaml_files:
        try:
            chunks.extend(_load_yaml(path, knowledge_dir))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "yaml_load_failed",
                path=str(path),
                error=str(exc),
            )

    logger.info(
        "knowledge_base_loaded",
        chunk_count=len(chunks),
        md_files=len(md_files),
        yaml_files=len(yaml_files),
    )
    return chunks


def _load_markdown(path: Path, root: Path) -> list[Chunk]:
    """Tek bir markdown dosyasını frontmatter ayrıştır + `##` bazında böler."""
    raw = path.read_text(encoding="utf-8")
    meta_dict, body = _split_frontmatter(raw)
    meta = _parse_meta(meta_dict, path)
    rel = path.relative_to(root).as_posix()

    sections = _split_by_h2(body.strip())
    if not sections:
        return []

    chunks: list[Chunk] = []
    for idx, (section_title, section_text) in enumerate(sections):
        # Chunk başlığı: doküman başlığı + section başlığı (varsa).
        if section_title is None:
            display_title = meta.title
            chunk_suffix = "intro"
        else:
            display_title = f"{meta.title} — {section_title}"
            chunk_suffix = _slugify(section_title) or f"section_{idx}"

        # Embedding'e verilecek metin: display_title + section içeriği.
        # Başlığı da metne dahil ediyoruz; sorunun başlıkla eşleşmesi
        # multilingual modelde belirgin fark yaratıyor.
        embed_text = f"{display_title}\n\n{section_text}".strip()
        if not embed_text:
            continue

        chunks.append(
            Chunk(
                chunk_id=f"md::{rel}::{idx:03d}_{chunk_suffix}",
                source_path=rel,
                source_title=display_title,
                category=meta.category,
                text=embed_text,
                asset_template=meta.asset_template,
                tags=meta.tags,
            )
        )
    return chunks


def _load_yaml(path: Path, root: Path) -> list[Chunk]:
    """YAML Q&A dosyasını okuyup her çifti bir chunk'a çevirir."""
    raw = path.read_text(encoding="utf-8")
    data = yaml.safe_load(raw) or {}
    if not isinstance(data, dict):
        logger.warning(
            "yaml_not_mapping",
            path=str(path),
        )
        return []

    items_raw = data.get("items")
    if not isinstance(items_raw, list):
        logger.warning(
            "yaml_items_missing",
            path=str(path),
        )
        return []

    meta = _parse_meta(data, path)
    rel = path.relative_to(root).as_posix()

    chunks: list[Chunk] = []
    for idx, item in enumerate(items_raw):
        if not isinstance(item, dict):
            continue
        question = str(item.get("q", "")).strip()
        answer = str(item.get("a", "")).strip()
        if not question or not answer:
            continue

        embed_text = f"Soru: {question}\nCevap: {answer}"
        display_title = f"{meta.title} — {_truncate(question, 60)}"
        chunks.append(
            Chunk(
                chunk_id=f"yaml::{rel}::{idx:03d}",
                source_path=rel,
                source_title=display_title,
                category=meta.category,
                text=embed_text,
                asset_template=meta.asset_template,
                tags=meta.tags,
                yaml_question=question,
            )
        )
    return chunks


def _split_frontmatter(raw: str) -> tuple[dict[str, object], str]:
    """Markdown dosyasındaki `---` frontmatter bloğunu ayırır.

    Frontmatter yoksa `({}, raw)` döner. Bloğu parse edemezsek de
    yine de gövdeyi döndür — ama log uyarısı.
    """
    match = _FRONTMATTER_RE.match(raw)
    if match is None:
        return {}, raw
    body_raw = match.group("body")
    rest = match.group("rest")
    try:
        parsed = yaml.safe_load(body_raw) or {}
    except yaml.YAMLError as exc:
        logger.warning("frontmatter_parse_error", error=str(exc))
        return {}, rest
    if not isinstance(parsed, dict):
        return {}, rest
    return parsed, rest


def _parse_meta(data: dict[str, object], path: Path) -> _DocMeta:
    """Ham frontmatter/YAML dict'ten tip güvenli `_DocMeta` üretir."""
    title_raw = data.get("title")
    if isinstance(title_raw, str) and title_raw.strip():
        title = title_raw.strip()
    else:
        title = path.stem.replace("_", " ").replace("-", " ").strip()

    category_raw = data.get("category")
    if isinstance(category_raw, str) and category_raw.strip() in _VALID_CATEGORIES:
        category = category_raw.strip()
    else:
        category = "diger"

    asset_template_raw = data.get("asset_template")
    if isinstance(asset_template_raw, str) and asset_template_raw.strip():
        asset_template: str | None = asset_template_raw.strip()
    else:
        asset_template = None

    tags_raw = data.get("tags")
    if isinstance(tags_raw, list):
        tags = tuple(str(t).strip() for t in tags_raw if str(t).strip())
    else:
        tags = ()

    return _DocMeta(
        title=title,
        category=category,
        asset_template=asset_template,
        tags=tags,
    )


def _split_by_h2(body: str) -> list[tuple[str | None, str]]:
    """Gövdeyi `##` başlıklarına göre bölüp (title, text) listesi döner.

    İlk `##` öncesi girizgah (`title=None`) ilk eleman olur (boş değilse).
    """
    matches = list(_H2_RE.finditer(body))
    if not matches:
        # Hiç `##` yok — tüm gövde tek chunk.
        if body.strip():
            return [(None, body.strip())]
        return []

    sections: list[tuple[str | None, str]] = []
    # İlk `##` öncesi girizgah
    first_start = matches[0].start()
    intro = body[:first_start].strip()
    if intro:
        # `#` (H1) başlıklarını temizle — zaten doküman title'ı frontmatter'da.
        intro_cleaned = re.sub(r"^#[ \t]+.+?$", "", intro, flags=re.MULTILINE).strip()
        if intro_cleaned:
            sections.append((None, intro_cleaned))

    for i, match in enumerate(matches):
        section_title = match.group("title").strip()
        text_start = match.end()
        text_end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        section_body = body[text_start:text_end].strip()
        # Section metninde section başlığını tekrarlamıyoruz — display_title
        # embedding'e zaten ekliyor.
        sections.append((section_title, section_body))

    return sections


def _slugify(text: str) -> str:
    """Chunk ID'de kullanılacak kısa slug üretir. Türkçe karakter dostu değil
    ama chunk_id sadece iç kimlikleme için — kullanıcıya gösterilmez."""
    lowered = text.lower()
    replaced = re.sub(r"[^a-z0-9]+", "_", lowered)
    return replaced.strip("_")[:40]


def _truncate(text: str, max_len: int) -> str:
    """Uzun string'i `max_len` karaktere `...` ile keser."""
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "…"
