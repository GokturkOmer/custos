"""`pdf_loader` birim testleri — dijital PDF extraction + render (Faz 1/A).

Fixture PROGRAMATİK + DETERMİNİSTİK üretilir (pymupdf ile): 3 sayfalık dijital
manuel — (1) düz metin, (2) 3×4 ızgara tablo, (3) gömülü görsel + altyazı.
Böylece OCR'a gerek kalmaz (gömülü text), commit'lenen binary yok, sonuç
pymupdf sürümünden bağımsız (asserts içerik üzerinde, byte üzerinde değil).
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from custos.assistant.pdf_loader import count_pages, render_and_extract

# Tablo sayfasındaki ızgara — 3 kolon × 4 satır (sabit koordinat = deterministik).
_TABLE_COLS_X = (72.0, 240.0, 400.0)
_TABLE_ROWS_Y = (110.0, 140.0, 170.0, 200.0)
_TABLE_CELLS = (
    ("Parameter", "Value", "Unit"),
    ("Capacity", "1200", "kW"),
    ("Voltage", "400", "V"),
    ("Current", "210", "A"),
)


def _build_sample_manual(path: Path) -> None:
    """Deterministik 3 sayfalık dijital PDF üretir (gömülü text + tablo + görsel)."""
    doc = pymupdf.open()

    # Sayfa 1 — düz metin (tek sol-marj → tablo değil, görsel değil).
    page1 = doc.new_page()
    page1.insert_text((72, 72), "Chiller Operation Manual", fontsize=18)
    page1.insert_text(
        (72, 120),
        "This manual describes operation and maintenance of the unit.\n"
        "Follow all safety instructions before servicing the equipment.\n"
        "Refer to the troubleshooting section for fault guidance.",
        fontsize=11,
    )

    # Sayfa 2 — 3×4 ızgara tablo (kolonlar birden çok satırda hizalı → tablo).
    page2 = doc.new_page()
    page2.insert_text((72, 72), "Technical Specifications", fontsize=14)
    for row_index, y in enumerate(_TABLE_ROWS_Y):
        for col_index, x in enumerate(_TABLE_COLS_X):
            page2.insert_text((x, y), _TABLE_CELLS[row_index][col_index], fontsize=11)

    # Sayfa 3 — gömülü görsel (block type 1) + altyazı metni.
    page3 = doc.new_page()
    page3.insert_text((72, 60), "Figure 1: Refrigerant cycle diagram", fontsize=12)
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 96, 96), False)
    pixmap.clear_with(180)
    page3.insert_image(pymupdf.Rect(72, 90, 200, 218), pixmap=pixmap)
    page3.insert_text((72, 240), "See the diagram above for the cooling cycle.", fontsize=11)

    doc.save(str(path))
    doc.close()


@pytest.fixture
def sample_pdf(tmp_path: Path) -> Path:
    """tmp_path altında örnek dijital manueli üretir, yolunu döner."""
    pdf_path = tmp_path / "sample_digital_manual.pdf"
    _build_sample_manual(pdf_path)
    return pdf_path


def test_count_pages(sample_pdf: Path) -> None:
    """Sayfa sayısı doğru okunmalı (render/extract olmadan)."""
    assert count_pages(sample_pdf) == 3


def test_render_and_extract_chunk_per_page(sample_pdf: Path, tmp_path: Path) -> None:
    """Bir sayfa = bir chunk; page_no 1-indeksli ve sıralı."""
    chunks = render_and_extract(
        pdf_path=sample_pdf,
        document_id=42,
        data_dir=tmp_path,
        render_dpi=100,
    )
    assert len(chunks) == 3
    assert [chunk.page_no for chunk in chunks] == [1, 2, 3]


def test_render_and_extract_text_filled(sample_pdf: Path, tmp_path: Path) -> None:
    """Her sayfada gömülü text dolu olmalı (dijital PDF, OCR gerekmez)."""
    chunks = render_and_extract(
        pdf_path=sample_pdf, document_id=42, data_dir=tmp_path, render_dpi=100
    )
    for chunk in chunks:
        assert chunk.text_content.strip()
    # Belirli içerik gömülü text'ten geliyor mu (render değil extraction).
    page_text = {chunk.page_no: chunk.text_content for chunk in chunks}
    assert "Chiller Operation Manual" in page_text[1]
    assert "Capacity" in page_text[2]


def test_render_and_extract_png_written(sample_pdf: Path, tmp_path: Path) -> None:
    """Her sayfa için PNG `pages/{document_id}/{page_no}.png` altına yazılmalı."""
    document_id = 42
    chunks = render_and_extract(
        pdf_path=sample_pdf, document_id=document_id, data_dir=tmp_path, render_dpi=100
    )
    for chunk in chunks:
        expected = tmp_path / "pages" / str(document_id) / f"{chunk.page_no}.png"
        assert Path(chunk.png_path) == expected
        assert expected.is_file()
        assert expected.stat().st_size > 0


def test_table_and_figure_heuristics(sample_pdf: Path, tmp_path: Path) -> None:
    """has_table/has_figure makul: tablo sayfası tablo, görsel sayfası figür."""
    chunks = render_and_extract(
        pdf_path=sample_pdf, document_id=42, data_dir=tmp_path, render_dpi=100
    )
    by_page = {chunk.page_no: chunk for chunk in chunks}
    # Sayfa 2 ızgara tablo; sayfa 1 düz metin.
    assert by_page[2].has_table is True
    assert by_page[1].has_table is False
    # Sayfa 3 gömülü görsel; sayfa 1 görselsiz.
    assert by_page[3].has_figure is True
    assert by_page[1].has_figure is False
