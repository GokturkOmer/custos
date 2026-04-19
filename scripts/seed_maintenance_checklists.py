"""4 generic bakım checklist'i seed eder.

Brief §4.8: Pilot öncesi sistemin nasıl çalıştığını göstermek için
generic checklist örnekleri. AVM'ye özgü (AHU filtre, cooling tower
dozaj vb.) checklist'ler F9 AVM Template Pack ile birlikte gelecek.

Idempotent: slug UNIQUE olduğu için aynı seed ikinci kez çalıştırılırsa
yeni kayıt eklemez.

Kullanım:
    python scripts/seed_maintenance_checklists.py
"""

from __future__ import annotations

import asyncio

import structlog

from custos.shared.config import Settings
from custos.shared.database import (
    MaintenanceChecklist,
    MaintenanceChecklistStep,
    TimescaleDBDatabase,
)

logger = structlog.get_logger(logger_name="seed_maintenance")


def _make_step(order: int, text: str, minutes: int | None = None) -> MaintenanceChecklistStep:
    """Seed için step nesnesi — checklist_id insert sırasında atanır."""
    return MaintenanceChecklistStep(
        checklist_id=0, sort_order=order, text=text, estimated_minutes=minutes,
    )


SEED_CHECKLISTS: list[MaintenanceChecklist] = [
    MaintenanceChecklist(
        slug="filtre-kontrolu",
        title="Filtre Kontrolü",
        description="Genel amaçlı filtre kontrol ve temizlik prosedürü.",
        category="generic",
        steps=[
            _make_step(0, "Filtreyi görsel olarak incele (toz, kir, deformasyon).", 2),
            _make_step(1, "Fark basınç sensörü varsa mevcut değeri not et.", 1),
            _make_step(2, "Gerektiğinde filtreyi söküp basınçlı hava ile temizle.", 10),
            _make_step(3, "Filtre çok kirli veya yırtık ise değiştir.", 15),
            _make_step(4, "Kontrol ve değişim tarihini sisteme kaydet.", 2),
        ],
    ),
    MaintenanceChecklist(
        slug="motor-genel-kontrol",
        title="Motor Genel Kontrol",
        description="Elektrik motorları için aylık rutin kontrol.",
        category="periodic",
        steps=[
            _make_step(0, "Motor yüzey sıcaklığını el/termal kamera ile kontrol et.", 3),
            _make_step(1, "Çalışma akımını pense ampermetre ile ölç.", 3),
            _make_step(2, "Mekanik titreşim ve ses anomalilerini dinle.", 3),
            _make_step(3, "Kablo bağlantılarını ve topraklama kontrolünü yap.", 5),
            _make_step(4, "Yağlama noktalarını kontrol et, gerekirse yağla.", 10),
            _make_step(5, "Bulguları not et ve sisteme işle.", 2),
        ],
    ),
    MaintenanceChecklist(
        slug="pompa-aylik-bakim",
        title="Pompa Aylık Bakım",
        description="Sirkülasyon ve basınçlandırma pompaları için aylık bakım.",
        category="periodic",
        steps=[
            _make_step(0, "Emiş ve deşarj basınç değerlerini oku.", 2),
            _make_step(1, "Motor akımını ölç, nominal değerle karşılaştır.", 3),
            _make_step(2, "Salmastra/mekanik salmastrayı kaçak açısından kontrol et.", 5),
            _make_step(3, "Titreşim ve ses anomalilerini dinle.", 3),
            _make_step(4, "Gövde sıcaklığını kontrol et.", 2),
            _make_step(5, "Kaplin ve yatak durumunu görsel incele.", 5),
            _make_step(6, "Bulguları sisteme kaydet.", 2),
        ],
    ),
    MaintenanceChecklist(
        slug="pano-gozle-kontrol",
        title="Pano Gözle Kontrol",
        description="Elektrik panosu rutin görsel kontrol — haftalık veya aylık.",
        category="generic",
        steps=[
            _make_step(0, "Pano kapı ve kilit mekanizmasını kontrol et.", 1),
            _make_step(1, "İç ve dış yüzeylerde toz birikimini kontrol et.", 2),
            _make_step(2, "Kabloların tutma bileziklerini ve terminal gevşekliklerini incele.", 5),
            _make_step(3, "Pilot lamba ve LED'lerin durumunu kontrol et.", 2),
            _make_step(4, "Etiket ve uyarı işaretlerinin okunabilir olduğunu doğrula.", 2),
            _make_step(5, "Kontrol tarihini pano iç etiketine yaz.", 1),
        ],
    ),
]


async def seed() -> None:
    """Seed işlemi. Çakışan slug'lar atlanır."""
    settings = Settings()
    db = TimescaleDBDatabase(settings)
    await db.connect()
    try:
        existing_slugs = {c.slug for c in await db.list_maintenance_checklists()}
        created = 0
        skipped = 0
        for checklist in SEED_CHECKLISTS:
            if checklist.slug in existing_slugs:
                await logger.ainfo(
                    "Checklist zaten mevcut, atlandı",
                    slug=checklist.slug,
                )
                skipped += 1
                continue
            await db.insert_maintenance_checklist(checklist)
            await logger.ainfo(
                "Checklist seed edildi",
                slug=checklist.slug, title=checklist.title,
            )
            created += 1
        await logger.ainfo(
            "Seed tamamlandı", created=created, skipped=skipped,
        )
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(seed())
