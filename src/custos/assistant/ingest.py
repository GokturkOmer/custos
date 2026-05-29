"""Asistan PDF ingest orchestration + CLI (Faz 1 / Bölüm A).

`pdf_loader` (saf extraction/render) ile `repository` (assistant şeması) arasını
bağlar. Bu modül SQL İÇERMEZ — tüm DB erişimi repository metotları üzerinden.

Sıra ÖNEMLİ: `document_id` hem PNG yoluna hem kaynak PDF adına girdiğinden önce
`insert_document` ile id alınır, SONRA sayfalar render edilip kaynak PDF
kopyalanır.

CLI:
    python -m custos.assistant.ingest <pdf_yolu> \\
        --equipment-type chiller --equipment-model "30XA-1002" --language en
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
from pathlib import Path

import structlog

from custos.assistant import pdf_loader
from custos.assistant.repository import AssistantRepository
from custos.shared.config import settings
from custos.shared.logging import configure_logging

logger = structlog.get_logger(logger_name="assistant.ingest")

# Plan §3 ekipman tipi seti — katı zorlama YOK; bilinmeyen tip yalnız uyarı
# loglar (pilotta serbest metin esnekliği korunur).
_KNOWN_EQUIPMENT_TYPES = frozenset(
    {"chiller", "ahu", "pump", "cooling_tower", "boiler", "vfd", "other"}
)


async def ingest_pdf(
    *,
    repository: AssistantRepository,
    pdf_path: Path,
    data_dir: Path,
    render_dpi: int,
    equipment_type: str | None,
    equipment_model: str | None,
    language: str | None,
    uploaded_by: str | None = None,
) -> int:
    """Tek bir dijital PDF'i ingest eder ve `document_id` döner.

    Adımlar (sıra önemli — id PNG/kaynak yollarına girer):
    1. PDF aç → `total_pages`.
    2. `insert_document` → `document_id`.
    3. Sayfaları `pages/{id}/` altına render et + text/heuristik çıkar.
    4. Kaynak PDF'yi `sources/{id}.pdf` altına kopyala.
    5. `ChunkInput` listesini `insert_chunks_batch` ile yaz.

    OCR YOK (Bölüm A dijital). `ocr_used` her zaman False.
    """
    if not pdf_path.is_file():
        msg = f"PDF bulunamadı: {pdf_path}"
        raise FileNotFoundError(msg)

    if equipment_type is not None and equipment_type not in _KNOWN_EQUIPMENT_TYPES:
        logger.warning(
            "ingest_bilinmeyen_equipment_type",
            equipment_type=equipment_type,
            bilinen=sorted(_KNOWN_EQUIPMENT_TYPES),
        )

    total_pages = pdf_loader.count_pages(pdf_path)

    # source_pdf_path Faz 1 Bölüm A'da NULL yazılır: kaynak yol document_id'ye
    # bağlıdır, ama document_id bu insert ile üretilir (döngüsel). 4 metotluk
    # Faz 1 yüzeyinde update yok; disk kopyası kanonik `sources/{id}.pdf`
    # konumuna yine de yazılır (yol gerektiğinde id'den yeniden türetilebilir).
    document_id = await repository.insert_document(
        filename=pdf_path.name,
        equipment_model=equipment_model,
        equipment_type=equipment_type,
        language=language,
        total_pages=total_pages,
        ocr_used=False,
        source_pdf_path=None,
        uploaded_by=uploaded_by,
    )

    chunks = pdf_loader.render_and_extract(
        pdf_path=pdf_path,
        document_id=document_id,
        data_dir=data_dir,
        render_dpi=render_dpi,
    )

    sources_dir = data_dir / "sources"
    sources_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(pdf_path, sources_dir / f"{document_id}.pdf")

    await repository.insert_chunks_batch(document_id, chunks)

    await logger.ainfo(
        "asistan_pdf_ingest_tamam",
        document_id=document_id,
        filename=pdf_path.name,
        total_pages=total_pages,
        chunk_count=len(chunks),
    )
    return document_id


def _build_arg_parser() -> argparse.ArgumentParser:
    """Ingest CLI argüman ayrıştırıcısını kurar."""
    parser = argparse.ArgumentParser(
        prog="python -m custos.assistant.ingest",
        description="Dijital PDF manuel ingest (Faz 1 Bölüm A — OCR yok).",
    )
    parser.add_argument("pdf_path", help="Ingest edilecek PDF dosyasının yolu.")
    parser.add_argument(
        "--equipment-type",
        default=None,
        help="Ekipman tipi (chiller/ahu/pump/cooling_tower/boiler/vfd/other).",
    )
    parser.add_argument(
        "--equipment-model",
        default=None,
        help="Ekipman modeli (serbest metin).",
    )
    parser.add_argument(
        "--language",
        default=None,
        help="Doküman dili (tr/en/mixed; serbest metin).",
    )
    return parser


async def _run_cli(args: argparse.Namespace) -> int:
    """CLI argümanlarıyla repository pool'unu açar, ingest eder, kapatır."""
    repository = AssistantRepository(settings)
    await repository.connect()
    try:
        return await ingest_pdf(
            repository=repository,
            pdf_path=Path(args.pdf_path),
            data_dir=Path(settings.custos_assistant_data_dir),
            render_dpi=settings.custos_assistant_render_dpi,
            equipment_type=args.equipment_type,
            equipment_model=args.equipment_model,
            language=args.language,
        )
    finally:
        await repository.close()


def main() -> None:
    """structlog'u kurar ve PDF ingest'i çalıştırır (CLI giriş noktası)."""
    args = _build_arg_parser().parse_args()
    configure_logging(settings.log_level)
    document_id = asyncio.run(_run_cli(args))
    logger.info("ingest_cli_bitti", document_id=document_id, pdf=args.pdf_path)


if __name__ == "__main__":
    main()
