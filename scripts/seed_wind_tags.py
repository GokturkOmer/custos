"""Wind türbin tag bulk-import scripti (Faz 1.3).

`tag_map_farm_a.csv` icindeki 81 sensör tag'ini ``custos_wind`` DB'sinin
``tags`` tablosuna yazar; CSV'deki `custos_tag_name` kolonu DB tag_id
olarak kullanilir. Migration 038'in INSERT...WHERE EXISTS pattern'i
sayesinde bu seed sonrasinda 038 yeniden tetiklenirse 25 default
threshold otomatik dolar.

Idempotent davranis:
- Asset instance: ayni ``name`` ile mevcutsa kullanir, yoksa olusturur.
- Tag: ``get_tag(tag_id)`` mevcut donerse atlanir.

Production guard:
- ``POSTGRES_DB`` env var ``custos_wind`` olmali; degilse abort.
- AVM ``custos`` ve endurance ``custos_endurance`` DB'lerine yazmaz.

Kullanim::

    set -a && source _personal/wind_pivot/.env.wind && set +a
    .venv/bin/python scripts/seed_wind_tags.py \\
        --tag-map _personal/wind_pivot/tag_map_farm_a.csv \\
        --asset-instance-name wind_turbine_01 \\
        --asset-template-slug wind_turbine_v1

Sonraki adimlar:
    1. ``alembic upgrade head`` — migration 038 zaten alembic_version'da
       ise threshold INSERT'leri tetiklenmez. Ayrintilar icin script
       sonu cikti hatirlatmasini oku.
    2. ``python scripts/csv_replay_simulator.py --asset-instance-id <id>``
       — Faz 1.2 replay simulator'i bu instance'i kullanir.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import os
import sys
from pathlib import Path

import structlog

from custos.shared.config import settings
from custos.shared.database import (
    AssetInstance,
    AssetTemplate,
    DatabaseInterface,
    TagRecord,
    create_database,
)
from custos.shared.logging import configure_logging

logger = structlog.get_logger(logger_name="seed_wind_tags")

# Yalnizca bu DB adi altinda calismaya izin verilir. AVM production ve
# endurance ortamlarini kaza ile kirletmemek icin guard.
EXPECTED_POSTGRES_DB = "custos_wind"

# Wind diagslave default endpoint (csv_replay_simulator.py default'lariyla
# uyumlu). Saha kurulumunda CLI override edilir.
DEFAULT_MODBUS_HOST = "127.0.0.1"
DEFAULT_MODBUS_PORT = 5021
DEFAULT_UNIT_ID = 1

# 1 dk polling — CARE dataset 10 dk aggregate ama replay simulator
# speed=1000 ile 0.6sn/tick yazar; collector daha hizli pollamasi gerekir.
DEFAULT_POLLING_INTERVAL_MS = 60_000
DEFAULT_POLLING_PRESET = "normal"

# Tag map CSV'sinde zorunlu kolonlar (eksiklik = parse hatasi).
REQUIRED_CSV_COLUMNS: tuple[str, ...] = (
    "custos_tag_name",
    "register_address",
    "register_type",
)


def load_tag_map(path: Path) -> list[dict[str, str]]:
    """``tag_map_farm_a.csv``i okur, kolon dogrulamasi yapar.

    Header'da zorunlu kolonlar yoksa ``ValueError`` firlatir; bu sayede
    yanlis CSV formati erken yakalanir.
    """
    with path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            msg = f"CSV bos veya header yok: {path}"
            raise ValueError(msg)
        missing = [c for c in REQUIRED_CSV_COLUMNS if c not in reader.fieldnames]
        if missing:
            msg = (
                f"CSV header eksik kolon iceriyor: {missing}. "
                f"Beklenen: {list(REQUIRED_CSV_COLUMNS)}"
            )
            raise ValueError(msg)
        return list(reader)


async def find_template_by_slug(
    db: DatabaseInterface,
    slug: str,
) -> AssetTemplate | None:
    """Tum template'leri listeleyip slug ile filtrele.

    DatabaseInterface'de slug-by-key arama yok; tek opsiyon list +
    Python filtre. Template sayisi kucuk (handful) oldugu icin
    performans sorunu degil.
    """
    templates = await db.list_asset_templates()
    return next((t for t in templates if t.slug == slug), None)


async def ensure_asset_instance(
    db: DatabaseInterface,
    *,
    name: str,
    template: AssetTemplate,
    description: str,
) -> tuple[AssetInstance, bool]:
    """Asset instance get-or-create. ``(instance, created)`` doner.

    Idempotent: ayni name + template_id kombinasyonu varsa yeniden
    olusturmaz, mevcuti doner.
    """
    assert template.id is not None, "Template id None — upsert sonrasi alinmali"
    existing = await db.list_asset_instances(template_id=template.id)
    for inst in existing:
        if inst.name == name:
            return inst, False

    new_inst = AssetInstance(
        template_id=template.id,
        name=name,
        description=description,
    )
    saved = await db.insert_asset_instance(new_inst)
    return saved, True


def build_tag_record(
    *,
    row: dict[str, str],
    modbus_host: str,
    modbus_port: int,
    unit_id: int,
    polling_interval_ms: int,
) -> TagRecord:
    """CSV satirini ``TagRecord``a cevirir.

    Bos/eksik ``gain``/``offset`` kolonlarinda default (1.0 / 0.0)
    kullanilir; description bos ise tag_id ile doldurulur.
    """
    tag_id = row["custos_tag_name"].strip()
    description = (row.get("description") or "").strip() or tag_id
    return TagRecord(
        tag_id=tag_id,
        name=description,
        modbus_host=modbus_host,
        modbus_port=modbus_port,
        unit_id=unit_id,
        register_address=int(row["register_address"]),
        register_type=row["register_type"].strip(),
        byte_order="big",
        gain=float((row.get("gain") or "1.0").strip() or 1.0),
        offset=float((row.get("offset") or "0.0").strip() or 0.0),
        unit=(row.get("unit") or "").strip(),
        polling_interval_ms=polling_interval_ms,
        polling_preset=DEFAULT_POLLING_PRESET,
        status="active",
    )


async def seed_tags(
    db: DatabaseInterface,
    rows: list[dict[str, str]],
    *,
    modbus_host: str,
    modbus_port: int,
    unit_id: int,
    polling_interval_ms: int,
) -> tuple[int, int]:
    """Tag'leri DB'ye yazar. ``(added, skipped)`` doner.

    Idempotency: her tag icin ``db.get_tag`` ile var olup olmadigi kontrol
    edilir; mevcutsa atlanir (skipped++).
    """
    added = 0
    skipped = 0
    for row in rows:
        tag_id = (row.get("custos_tag_name") or "").strip()
        if not tag_id:
            # Boş custos_tag_name → skip (CSV'de yanlislikla satir kalsa bile).
            continue
        existing = await db.get_tag(tag_id)
        if existing is not None:
            skipped += 1
            continue
        tag = build_tag_record(
            row=row,
            modbus_host=modbus_host,
            modbus_port=modbus_port,
            unit_id=unit_id,
            polling_interval_ms=polling_interval_ms,
        )
        await db.insert_tag(tag)
        added += 1
    return added, skipped


def check_postgres_db_guard() -> str | None:
    """``POSTGRES_DB`` env var dogru DB'yi gosteriyor mu?

    Yanlissa hata mesaji string'i doner; dogru ise None.
    """
    current = os.environ.get("POSTGRES_DB")
    if current != EXPECTED_POSTGRES_DB:
        return (
            f"HATA: POSTGRES_DB={current!r} "
            f"(beklenen {EXPECTED_POSTGRES_DB!r}). "
            f"Once .env.wind kaynaklayin:\n"
            f"  set -a && source _personal/wind_pivot/.env.wind && set +a"
        )
    return None


def _print_completion_notes(
    instance: AssetInstance,
    added: int,
    skipped: int,
    total: int,
) -> None:
    """Operatöre özet + sonraki adim hatirlatmasi (stdout)."""
    print()  # noqa: T201
    print("=" * 60)  # noqa: T201
    print("Wind tag bulk-import tamamlandi")  # noqa: T201
    print("=" * 60)  # noqa: T201
    print(f"  asset_instance_id : {instance.id}")  # noqa: T201
    print(f"  asset_instance    : {instance.name}")  # noqa: T201
    print(f"  template_id       : {instance.template_id}")  # noqa: T201
    print(f"  tag eklendi       : {added}")  # noqa: T201
    print(f"  tag atlandi (var) : {skipped}")  # noqa: T201
    print(f"  toplam CSV satiri : {total}")  # noqa: T201
    print()  # noqa: T201
    print("Sonraki adimlar:")  # noqa: T201
    print(  # noqa: T201
        "  1) Threshold'lar: migration 038 zaten alembic_version'da; "
        "INSERT'leri tetiklemek icin elle SQL veya stamp+upgrade.",
    )
    print(  # noqa: T201
        "     Hizli: psql -d custos_wind -c "
        '"DELETE FROM alembic_version WHERE version_num=\'038\';" '
        "&& alembic upgrade head",
    )
    print(  # noqa: T201
        "  2) Replay: python scripts/csv_replay_simulator.py "
        f"--asset-instance-id {instance.id} ...",
    )
    print()  # noqa: T201


async def run(args: argparse.Namespace) -> int:
    """Ana orchestrator: guard → tag map → instance → tag seed → output."""
    guard_err = check_postgres_db_guard()
    if guard_err:
        print(guard_err, file=sys.stderr)  # noqa: T201
        return 2

    tag_path = Path(args.tag_map)
    if not tag_path.exists():
        print(f"HATA: Tag map dosyasi yok: {tag_path}", file=sys.stderr)  # noqa: T201
        return 2

    try:
        rows = load_tag_map(tag_path)
    except ValueError as exc:
        print(f"HATA: Tag map parse: {exc}", file=sys.stderr)  # noqa: T201
        return 2

    await logger.ainfo(
        "Tag map yuklendi",
        path=str(tag_path),
        row_count=len(rows),
    )

    db = create_database(settings)
    await db.connect()
    try:
        template = await find_template_by_slug(db, args.asset_template_slug)
        if template is None:
            print(  # noqa: T201
                f"HATA: Asset template bulunamadi (slug={args.asset_template_slug!r}). "
                f"Once: python scripts/seed_asset_templates.py --dir data/asset_templates",
                file=sys.stderr,
            )
            return 3

        instance, created = await ensure_asset_instance(
            db,
            name=args.asset_instance_name,
            template=template,
            description=args.asset_instance_description,
        )
        await logger.ainfo(
            "Asset instance hazir",
            id=instance.id,
            name=instance.name,
            template_slug=template.slug,
            created=created,
        )

        added, skipped = await seed_tags(
            db,
            rows,
            modbus_host=args.modbus_host,
            modbus_port=args.modbus_port,
            unit_id=args.unit_id,
            polling_interval_ms=args.polling_interval_ms,
        )
        await logger.ainfo(
            "Tag seed tamamlandi",
            added=added,
            skipped=skipped,
            total=len(rows),
        )
    finally:
        await db.close()

    _print_completion_notes(instance, added, skipped, len(rows))
    return 0


def build_argparser() -> argparse.ArgumentParser:
    """CLI argumanlari (Faz 1.3 spec'i)."""
    parser = argparse.ArgumentParser(
        description=(
            "Wind türbin tag bulk-import. tag_map_farm_a.csv → custos_wind DB. "
            "Asset instance idempotent, tag insert idempotent."
        ),
    )
    parser.add_argument(
        "--tag-map",
        required=True,
        help="tag_map_farm_a.csv yolu.",
    )
    parser.add_argument(
        "--asset-instance-name",
        default="wind_turbine_01",
        help="Asset instance adi (default: wind_turbine_01).",
    )
    parser.add_argument(
        "--asset-template-slug",
        default="wind_turbine_v1",
        help="Asset template slug (default: wind_turbine_v1).",
    )
    parser.add_argument(
        "--asset-instance-description",
        default="Fraunhofer CARE Wind Farm A replay turbin",
        help="Asset instance aciklamasi (yeni olusturuluyorsa).",
    )
    parser.add_argument(
        "--modbus-host",
        default=DEFAULT_MODBUS_HOST,
        help=f"Tag modbus_host (default: {DEFAULT_MODBUS_HOST}).",
    )
    parser.add_argument(
        "--modbus-port",
        type=int,
        default=DEFAULT_MODBUS_PORT,
        help=f"Tag modbus_port (default: {DEFAULT_MODBUS_PORT} — wind diagslave).",
    )
    parser.add_argument(
        "--unit-id",
        type=int,
        default=DEFAULT_UNIT_ID,
        help=f"Tag modbus unit_id (default: {DEFAULT_UNIT_ID}).",
    )
    parser.add_argument(
        "--polling-interval-ms",
        type=int,
        default=DEFAULT_POLLING_INTERVAL_MS,
        help=(
            f"Collector polling interval ms (default: {DEFAULT_POLLING_INTERVAL_MS} "
            "— 1 dakika; CARE 10 dk aggregate ama replay daha hizli)."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry-point — argparse + asyncio.run wrapper."""
    configure_logging("INFO")
    parser = build_argparser()
    args = parser.parse_args(argv)
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
