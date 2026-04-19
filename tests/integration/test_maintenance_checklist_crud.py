"""Maintenance checklist CRUD entegrasyon testleri.

TimescaleDB'nin ayakta olmasını gerektirir (docker compose up -d).
"""

from __future__ import annotations

import uuid

import pytest

from custos.shared.database import (
    MaintenanceChecklist,
    MaintenanceChecklistStep,
    TimescaleDBDatabase,
)


def _make_checklist(slug_suffix: str = "") -> MaintenanceChecklist:
    """Test için örnek checklist (slug benzersiz olsun diye uuid)."""
    unique = uuid.uuid4().hex[:8]
    return MaintenanceChecklist(
        slug=f"test-{slug_suffix}-{unique}" if slug_suffix else f"test-{unique}",
        title="Test Bakım",
        description="Test amaçlı checklist",
        category="generic",
        steps=[
            MaintenanceChecklistStep(
                checklist_id=0,  # insert sırasında override edilecek
                sort_order=0,
                text="Adım 1 — görsel kontrol",
                estimated_minutes=5,
            ),
            MaintenanceChecklistStep(
                checklist_id=0,
                sort_order=1,
                text="Adım 2 — ölçüm",
                estimated_minutes=10,
            ),
        ],
    )


@pytest.mark.usefixtures("_check_db_available")
async def test_insert_checklist_with_steps(db: TimescaleDBDatabase) -> None:
    """Checklist ve adımları aynı anda kaydedilir."""
    c = _make_checklist("insert")
    created = await db.insert_maintenance_checklist(c)
    assert created.id is not None
    assert created.slug.startswith("test-insert-")
    assert len(created.steps) == 2
    assert created.steps[0].text.startswith("Adım 1")
    assert created.steps[0].id is not None
    assert created.steps[1].sort_order == 1


@pytest.mark.usefixtures("_check_db_available")
async def test_get_checklist_returns_steps_sorted(
    db: TimescaleDBDatabase,
) -> None:
    """Get adım sorguyla birlikte dönmeli, sort_order'a göre sıralı."""
    created = await db.insert_maintenance_checklist(_make_checklist("get"))
    assert created.id is not None

    fetched = await db.get_maintenance_checklist(created.id)
    assert fetched is not None
    assert len(fetched.steps) == 2
    assert fetched.steps[0].sort_order == 0
    assert fetched.steps[1].sort_order == 1


@pytest.mark.usefixtures("_check_db_available")
async def test_update_checklist(db: TimescaleDBDatabase) -> None:
    """Update başlık ve kategori değiştirir, steps dokunmaz."""
    created = await db.insert_maintenance_checklist(_make_checklist("upd"))
    assert created.id is not None

    updated = await db.update_maintenance_checklist(
        created.id, {"title": "Yeni Başlık", "category": "periodic"},
    )
    assert updated is not None
    assert updated.title == "Yeni Başlık"
    assert updated.category == "periodic"
    assert len(updated.steps) == 2  # steps korunmalı


@pytest.mark.usefixtures("_check_db_available")
async def test_delete_checklist_cascades_steps(
    db: TimescaleDBDatabase,
) -> None:
    """Checklist silinince adımlar CASCADE ile silinir."""
    created = await db.insert_maintenance_checklist(_make_checklist("del"))
    assert created.id is not None

    step_id = created.steps[0].id
    assert step_id is not None

    assert await db.delete_maintenance_checklist(created.id) is True
    assert await db.get_maintenance_checklist(created.id) is None
    assert await db.delete_maintenance_checklist(999999) is False

    # Step'lerin de silindiğini doğrula
    pool = db._get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id FROM maintenance_checklist_steps WHERE id = $1",
            step_id,
        )
    assert row is None


@pytest.mark.usefixtures("_check_db_available")
async def test_list_checklists_filter_by_category(
    db: TimescaleDBDatabase,
) -> None:
    """Kategori filtresi çalışır, liste steps ile birlikte döner."""
    c1 = _make_checklist("cat1")
    c1.category = "periodic"
    await db.insert_maintenance_checklist(c1)

    c2 = _make_checklist("cat2")
    c2.category = "alarm"
    await db.insert_maintenance_checklist(c2)

    periodic = await db.list_maintenance_checklists(category="periodic")
    periodic_test = [c for c in periodic if c.slug.startswith("test-")]
    assert len(periodic_test) >= 1
    assert all(c.category == "periodic" for c in periodic_test)
    assert all(len(c.steps) == 2 for c in periodic_test)


@pytest.mark.usefixtures("_check_db_available")
async def test_replace_checklist_steps(db: TimescaleDBDatabase) -> None:
    """Tüm adımlar atomik olarak yenileriyle değiştirilir."""
    created = await db.insert_maintenance_checklist(_make_checklist("rep"))
    assert created.id is not None

    new_steps = [
        MaintenanceChecklistStep(
            checklist_id=created.id, sort_order=0,
            text="Yeni A", estimated_minutes=3,
        ),
        MaintenanceChecklistStep(
            checklist_id=created.id, sort_order=1,
            text="Yeni B", estimated_minutes=4,
        ),
        MaintenanceChecklistStep(
            checklist_id=created.id, sort_order=2,
            text="Yeni C", estimated_minutes=5,
        ),
    ]
    result = await db.replace_maintenance_checklist_steps(
        created.id, new_steps,
    )
    assert len(result) == 3
    assert result[0].text == "Yeni A"
    assert result[2].text == "Yeni C"

    # Get sonrası da yeni adımları görmeli
    fetched = await db.get_maintenance_checklist(created.id)
    assert fetched is not None
    assert len(fetched.steps) == 3


@pytest.mark.usefixtures("_check_db_available")
async def test_update_checklist_rejects_invalid_fields(
    db: TimescaleDBDatabase,
) -> None:
    """Bilinmeyen alan güncellenmeye çalışılırsa ValueError."""
    created = await db.insert_maintenance_checklist(_make_checklist("inv"))
    assert created.id is not None

    with pytest.raises(ValueError, match="Güncellenemeyen alanlar"):
        await db.update_maintenance_checklist(created.id, {"slug": "new-slug"})
