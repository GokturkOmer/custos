"""F9 AVM Template Pack seed runner.

Kök dizindeki ``templates/`` klasöründeki YAML dosyalarını okur, pydantic ile
doğrular, ``DatabaseInterface.upsert_asset_template`` üzerinden idempotent
biçimde veritabanına yazar. Alarm ve bakım varsayılanları yalnızca YAML'da
taşınır (dashboard preview), seed adımında DB'ye yazılmaz.

Kullanım::

    python scripts/seed_asset_templates.py
    python scripts/seed_asset_templates.py --dir /alt/dizin/templates

CLAUDE.md: abstract DB arayüzü zorunlu, doğrudan SQL çağrısı yok.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import structlog

from custos.analytics.templates import (
    TemplateLoadError,
    default_template_dir,
    load_templates,
)
from custos.shared.config import Settings
from custos.shared.database import AuditLogEntry, TimescaleDBDatabase

logger = structlog.get_logger(logger_name="seed_asset_templates")


async def seed(template_dir: Path | None = None) -> int:
    """YAML şablonlarını DB'ye upsert eder.

    Dönüş: başarılı seed edilen şablon sayısı. Hata durumunda exception
    yayılır (CI/CLI fail).
    """
    target_dir = template_dir if template_dir is not None else default_template_dir()
    loaded = load_templates(target_dir)
    if not loaded:
        await logger.awarning("Hiç template YAML'ı bulunamadı", directory=str(target_dir))
        return 0

    settings = Settings()
    db = TimescaleDBDatabase(settings)
    await db.connect()
    try:
        count = 0
        for entry in loaded:
            tmpl = entry.schema.to_asset_template()
            upserted = await db.upsert_asset_template(tmpl)
            await logger.ainfo(
                "Template upsert edildi",
                slug=upserted.slug,
                roles=len(upserted.roles),
                kpis=len(upserted.kpi_definitions),
                path=str(entry.path.name),
            )
            count += 1

        await db.insert_audit_log(AuditLogEntry(
            category="seed",
            action="avm_template_pack",
            detail=f"{count} asset template upsert edildi",
        ))
        await logger.ainfo("Seed tamamlandı", total=count, directory=str(target_dir))
    finally:
        await db.close()

    return count


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AVM asset template seed runner")
    parser.add_argument(
        "--dir",
        type=Path,
        default=None,
        help="Template YAML dizini (varsayılan: proje kökündeki templates/)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        count = asyncio.run(seed(template_dir=args.dir))
    except TemplateLoadError as exc:
        print(f"YAML hatası: {exc}", file=sys.stderr)
        return 2
    return 0 if count >= 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
