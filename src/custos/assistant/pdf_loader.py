"""Dijital PDF ingest çekirdeği — sayfa-bazlı text extraction + PNG render.

Faz 1 / Bölüm A kapsamı: yalnız **gömülü-text'li (dijital)** PDF'ler. OCR
(taranmış sayfa fallback) ve pdfplumber fallback Bölüm B'ye aittir — burada YOK.

Bu modül SQL İÇERMEZ ve repository'yi ÇAĞIRMAZ; saf veri (`ChunkInput` listesi)
döner. DB'ye yazma orchestration (`ingest.py`) işidir. Böylece pymupdf'e bağlı
extraction/render mantığı DB olmadan test edilebilir.

Strateji (kanonik plan §4 Faz 1):
- Sayfa-bazlı gömülü text: `page.get_text("text")`.
- Sayfa PNG render @ `custos_assistant_render_dpi` (=200) →
  `{data_dir}/pages/{document_id}/{page_no}.png` (page_no 1-indeksli).
- `has_table` / `has_figure`: `page.get_text("dict")` blok analizi (BASİT
  heuristik — mükemmel değil, pilot kalitesinde "makul" yeterli).
- Bir sayfa = bir `ChunkInput` (page_no + text_content + png_path + has_*).
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

import pymupdf
import structlog

from custos.assistant.repository import ChunkInput

logger = structlog.get_logger(logger_name="assistant.pdf_loader")

# Tablo/şekil heuristik sabitleri. Koordinatlar PDF puntosu (72 dpi user space)
# cinsindendir — render DPI'dan bağımsız.
_ALIGN_BUCKET = 5.0  # ~5pt hizalama toleransı (kolon/satır gruplama)
_TABLE_MIN_ROWS = 3  # bir kolonun tablo sayılması için min farklı satır
_TABLE_MIN_COLS = 2  # tablo için min hizalı kolon sayısı
_BLOCK_TYPE_TEXT = 0
_BLOCK_TYPE_IMAGE = 1


def count_pages(pdf_path: Path) -> int:
    """PDF'in toplam sayfa sayısını döner (render/extract yapmadan).

    Orchestration `insert_document`'ı çağırmadan ÖNCE `total_pages`'i bilmek
    için kullanır (document_id PNG yoluna girdiğinden insert render'dan önce
    gelir).
    """
    with pymupdf.open(str(pdf_path)) as doc:  # type: ignore[no-untyped-call]
        return int(doc.page_count)


def render_and_extract(
    *,
    pdf_path: Path,
    document_id: int,
    data_dir: Path,
    render_dpi: int,
) -> list[ChunkInput]:
    """Dijital PDF'i sayfa-bazlı işler ve `ChunkInput` listesi döner.

    Her sayfa için: gömülü text çıkarılır, basit tablo/şekil heuristiği
    uygulanır ve sayfa `{data_dir}/pages/{document_id}/{page_no}.png` altına
    `render_dpi` çözünürlükte render edilir (page_no 1-indeksli). Dizin yoksa
    oluşturulur (dev'de data_dir setup.sh ile kurulmamış olabilir).

    OCR YOK: text'siz (taranmış) sayfa boş `text_content` ile yine de chunk
    olur — OCR fallback Bölüm B'de eklenecek.
    """
    pages_dir = data_dir / "pages" / str(document_id)
    pages_dir.mkdir(parents=True, exist_ok=True)

    # DPI → zoom matrisi (pymupdf koordinatları 72 dpi tabanlı).
    zoom = render_dpi / 72.0
    matrix = pymupdf.Matrix(zoom, zoom)  # type: ignore[no-untyped-call]

    chunks: list[ChunkInput] = []
    with pymupdf.open(str(pdf_path)) as doc:  # type: ignore[no-untyped-call]
        for page_index in range(doc.page_count):
            page = doc[page_index]
            page_no = page_index + 1

            text_content = str(page.get_text("text")).strip()
            layout: dict[str, Any] = page.get_text("dict")
            has_figure = _detect_figure(layout)
            has_table = _detect_table(layout)

            png_path = pages_dir / f"{page_no}.png"
            pixmap = page.get_pixmap(matrix=matrix)
            pixmap.save(str(png_path))

            chunks.append(
                ChunkInput(
                    page_no=page_no,
                    text_content=text_content,
                    png_path=str(png_path),
                    has_table=has_table,
                    has_figure=has_figure,
                )
            )

    logger.info(
        "pdf_render_extract_tamam",
        document_id=document_id,
        page_count=len(chunks),
        render_dpi=render_dpi,
    )
    return chunks


def _detect_figure(layout: dict[str, Any]) -> bool:
    """Sayfada gömülü görsel var mı — `get_text("dict")` blok tipi 1 = image."""
    return any(
        block.get("type") == _BLOCK_TYPE_IMAGE
        for block in layout.get("blocks", [])
    )


def _detect_table(layout: dict[str, Any]) -> bool:
    """Basit ızgara/tablo heuristiği (BASİT — mükemmel değil).

    Text span'lerinin sol kenarlarını (`bbox[0]`) kolon kovalarına, satır
    üst kenarlarını (`bbox[1]`) satır kovalarına yuvarlar. En az
    `_TABLE_MIN_COLS` kolon, her biri en az `_TABLE_MIN_ROWS` farklı satırda
    hizalıysa sayfa "tablo içeriyor" sayılır.

    Düz metin tek sol-marj (1 kolon) → tablo değil. Bilinen sınır: gerçek
    çok-kolon (gazete/dergi) düzeni yanlışlıkla tablo görünebilir; pilotta
    kabul edilebilir.
    """
    column_rows: dict[int, set[int]] = defaultdict(set)
    for block in layout.get("blocks", []):
        if block.get("type") != _BLOCK_TYPE_TEXT:
            continue
        for line in block.get("lines", []):
            line_bbox = line.get("bbox")
            if not line_bbox:
                continue
            row_bucket = int(line_bbox[1] // _ALIGN_BUCKET)
            for span in line.get("spans", []):
                span_bbox = span.get("bbox")
                if not span_bbox:
                    continue
                col_bucket = int(span_bbox[0] // _ALIGN_BUCKET)
                column_rows[col_bucket].add(row_bucket)

    aligned_columns = sum(
        1 for rows in column_rows.values() if len(rows) >= _TABLE_MIN_ROWS
    )
    return aligned_columns >= _TABLE_MIN_COLS
