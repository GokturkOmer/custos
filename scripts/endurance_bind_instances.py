"""Endurance testi için 5 AVM instance'ı yaratır ve tag binding'lerini kurar.

F9 AVM template pack'i `seed_asset_templates.py` ile DB'ye upsert edilmiş
olmalıdır. Bu script template'leri slug üzerinden eşler, instance oluşturur
(Chiller-1, AHU-1, FCU-1, Booster-1, Sirkulasyon-1) ve her instance'ın
required role'lerini endurance kataloğundaki tag'lere bağlar.

Binding mantığı "tipten bağımsız" — endurance tag'leri gerçek fiziksel
büyüklükleri taşımaz, amaç collector→DB→KPI→anomaly hattında veri akışı
test etmektir. Bu yüzden role_key'e tip uyumundan çok "unique tag" olarak
yaklaşılır; amaç 7 gün boyunca instance'ların aktif kalması ve
`train_anomaly_models.py`'in her birine Isolation Forest eğitmesidir.

Idempotent: aynı instance_name varsa atlar, aynı (instance, role) binding'i
varsa yenisini eklemez.

Kullanım:
    python scripts/endurance_bind_instances.py
    python scripts/endurance_bind_instances.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import structlog

from custos.shared.config import settings
from custos.shared.database import (
    AssetInstance,
    AssetTemplate,
    DatabaseInterface,
    TagBinding,
    TemplateRole,
    create_database,
)

logger = structlog.get_logger(logger_name="endurance_bind")


# Template slug → instance adı + role → endurance tag_id eşlemeleri.
# Role anahtarları F9 template YAML'larından alındı; her template için
# `required=True` role'lere asgari binding sağlanır. Kullanılan tag'ler
# endurance kataloğunun farklı bölümlerinden (temp, pressure, energy,
# RPM, status) seçildi ki Isolation Forest farklı varyans görür.
BINDING_PLAN: list[dict[str, object]] = [
    {
        "template_slug": "chiller",
        "instance_name": "Chiller-1",
        "location": "Endurance WSL — sanal makine odası",
        "roles": {
            "supply_temp": "T001",
            "return_temp": "T002",
            "compressor_current": "T151",  # rpm tag (proxy — simülasyon amacı)
        },
    },
    {
        "template_slug": "ahu",
        "instance_name": "AHU-1",
        "location": "Endurance WSL — klima santrali",
        "roles": {
            "supply_air_temp": "T011",
            "return_air_temp": "T012",
            "supply_fan_status": "T181",  # durum biti
        },
    },
    {
        "template_slug": "fcu",
        "instance_name": "FCU-1",
        "location": "Endurance WSL — ofis",
        "roles": {
            "room_temp": "T021",
            "setpoint": "T022",
            "fan_status": "T182",
        },
    },
    {
        "template_slug": "booster_pump_set",
        "instance_name": "Booster-1",
        "location": "Endurance WSL — kazan dairesi",
        "roles": {
            "suction_pressure": "T051",
            "discharge_pressure": "T052",
            "pump1_status": "T183",
            "pump2_status": "T184",
        },
    },
    {
        "template_slug": "circulation_pump",
        "instance_name": "Sirkulasyon-1",
        "location": "Endurance WSL — soğuk su kolektörü",
        "roles": {
            "suction_pressure": "T061",
            "discharge_pressure": "T062",
            "motor_current": "T152",
        },
    },
]


async def _find_instance_by_name(
    db: DatabaseInterface,
    name: str,
) -> AssetInstance | None:
    """İsme göre instance ara (list_asset_instances kullanır — abstract API)."""
    for inst in await db.list_asset_instances():
        if inst.name == name:
            return inst
    return None


async def _existing_bindings(
    db: DatabaseInterface,
    instance_id: int,
) -> set[tuple[int, str]]:
    """Var olan (role_id, tag_id) kombinasyonları — duplicate önlemek için."""
    bindings = await db.list_tag_bindings(instance_id)
    return {(b.role_id, b.tag_id) for b in bindings}


async def _upsert_instance(
    db: DatabaseInterface,
    template: AssetTemplate,
    plan: dict[str, object],
) -> AssetInstance:
    """İstenen isimde instance yoksa yaratır, varsa var olanı döner."""
    name = str(plan["instance_name"])
    existing = await _find_instance_by_name(db, name)
    if existing is not None:
        await logger.ainfo("Instance mevcut, atlanıyor", name=name, id=existing.id)
        return existing

    assert template.id is not None
    instance = AssetInstance(
        template_id=template.id,
        name=name,
        description="Endurance testi — 7 gün kesintisiz trafik",
        location=str(plan.get("location", "")),
        status="active",
    )
    saved = await db.insert_asset_instance(instance)
    await logger.ainfo(
        "Instance oluşturuldu",
        name=saved.name,
        id=saved.id,
        template_slug=template.slug,
    )
    return saved


async def _bind_role_to_tag(
    db: DatabaseInterface,
    instance: AssetInstance,
    role: TemplateRole,
    tag_id: str,
    existing: set[tuple[int, str]],
    dry_run: bool,
) -> bool:
    """Tek bir (instance, role) → tag binding'ini ekler. Döndürülen: eklendi mi."""
    assert instance.id is not None
    assert role.id is not None
    key = (role.id, tag_id)
    if key in existing:
        await logger.ainfo(
            "Binding mevcut, atlanıyor",
            instance=instance.name,
            role=role.role_key,
            tag_id=tag_id,
        )
        return False

    if dry_run:
        await logger.ainfo(
            "(dry-run) Binding planlandı",
            instance=instance.name,
            role=role.role_key,
            tag_id=tag_id,
        )
        return False

    await db.insert_tag_binding(
        TagBinding(instance_id=instance.id, role_id=role.id, tag_id=tag_id),
    )
    existing.add(key)
    await logger.ainfo(
        "Binding eklendi",
        instance=instance.name,
        role=role.role_key,
        tag_id=tag_id,
    )
    return True


async def run(db: DatabaseInterface, dry_run: bool = False) -> tuple[int, int]:
    """Tüm binding planını uygular. (instance_count, binding_count) döndürür."""
    templates = {t.slug: t for t in await db.list_asset_templates()}
    if not templates:
        await logger.awarning(
            "Template bulunamadı — önce seed_asset_templates.py çalıştırın",
        )
        return (0, 0)

    instances_created = 0
    bindings_added = 0
    for plan in BINDING_PLAN:
        slug = str(plan["template_slug"])
        template = templates.get(slug)
        if template is None:
            await logger.awarning("Template eksik, plan atlandı", slug=slug)
            continue

        instance = await _upsert_instance(db, template, plan)
        if instance.id is None:
            continue
        instances_created += 1

        role_by_key = {r.role_key: r for r in template.roles}
        existing = await _existing_bindings(db, instance.id)
        roles_to_bind = plan["roles"]
        assert isinstance(roles_to_bind, dict)
        for role_key, tag_id in roles_to_bind.items():
            role = role_by_key.get(role_key)
            if role is None:
                await logger.awarning(
                    "Role template'te yok, atlandı",
                    template=slug,
                    role_key=role_key,
                )
                continue
            added = await _bind_role_to_tag(
                db, instance, role, str(tag_id), existing, dry_run,
            )
            if added:
                bindings_added += 1

    return (instances_created, bindings_added)


async def main(dry_run: bool) -> int:
    """CLI entry point."""
    db = create_database(settings)
    await db.connect()
    try:
        instance_count, binding_count = await run(db, dry_run=dry_run)
    finally:
        await db.close()

    await logger.ainfo(
        "Endurance binding tamamlandı",
        instance_sayısı=instance_count,
        binding_eklenen=binding_count,
        dry_run=dry_run,
    )
    return 0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Endurance testi için 5 AVM instance + tag binding kurar",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="DB'ye yazmadan planı logla (sanity check)",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args()
    sys.exit(asyncio.run(main(args.dry_run)))
