"""Maintenance schedule CRUD entegrasyon testleri.

TimescaleDB'nin ayakta olmasını gerektirir.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest

from custos.shared.database import (
    MaintenanceChecklist,
    MaintenanceChecklistStep,
    MaintenanceSchedule,
    TimescaleDBDatabase,
)


async def _ensure_checklist(db: TimescaleDBDatabase) -> int:
    """Schedule'ın referans verebileceği bir checklist oluşturur."""
    unique = uuid.uuid4().hex[:8]
    c = await db.insert_maintenance_checklist(
        MaintenanceChecklist(
            slug=f"test-sched-{unique}",
            title="Test Schedule CL",
            steps=[
                MaintenanceChecklistStep(
                    checklist_id=0,
                    sort_order=0,
                    text="Adım",
                    estimated_minutes=5,
                ),
            ],
        ),
    )
    assert c.id is not None
    return c.id


async def _ensure_asset_instance(db: TimescaleDBDatabase) -> int | None:
    """Mevcut ilk asset instance'ın id'sini döndürür, yoksa None."""
    instances = await db.list_asset_instances()
    return instances[0].id if instances else None


@pytest.mark.usefixtures("_check_db_available")
async def test_insert_schedule_with_instance(
    db: TimescaleDBDatabase,
) -> None:
    """Asset instance bazlı schedule oluşturma."""
    cid = await _ensure_checklist(db)
    iid = await _ensure_asset_instance(db)
    if iid is None:
        pytest.skip("Henüz asset instance yok, schedule testi atlanıyor")

    sched = MaintenanceSchedule(
        checklist_id=cid,
        asset_instance_id=iid,
        period_kind="monthly",
        period_value=1,
        anchor_date=date(2026, 5, 1),
        next_due_at=datetime(2026, 5, 1, 9, 0, tzinfo=UTC),
    )
    created = await db.insert_maintenance_schedule(sched)
    assert created.id is not None
    assert created.period_kind == "monthly"
    assert created.asset_instance_id == iid
    assert created.enabled is True


@pytest.mark.usefixtures("_check_db_available")
async def test_insert_schedule_xor_constraint(
    db: TimescaleDBDatabase,
) -> None:
    """Template ve instance her ikisi birden verilirse DB reddetmeli."""
    cid = await _ensure_checklist(db)
    sched = MaintenanceSchedule(
        checklist_id=cid,
        asset_template_id=1,
        asset_instance_id=1,  # İkisi birden — CHECK reddetmeli
        period_kind="daily",
        anchor_date=date(2026, 5, 1),
        next_due_at=datetime(2026, 5, 1, tzinfo=UTC),
    )
    with pytest.raises(Exception, match="check constraint"):
        await db.insert_maintenance_schedule(sched)


@pytest.mark.usefixtures("_check_db_available")
async def test_update_schedule(db: TimescaleDBDatabase) -> None:
    """Schedule güncelleme — enabled, period_value değiştir."""
    cid = await _ensure_checklist(db)
    iid = await _ensure_asset_instance(db)
    if iid is None:
        pytest.skip("asset instance yok")

    sched = MaintenanceSchedule(
        checklist_id=cid,
        asset_instance_id=iid,
        period_kind="weekly",
        anchor_date=date(2026, 5, 1),
        next_due_at=datetime(2026, 5, 1, tzinfo=UTC),
    )
    created = await db.insert_maintenance_schedule(sched)
    assert created.id is not None

    updated = await db.update_maintenance_schedule(
        created.id,
        {"enabled": False, "period_value": 2},
    )
    assert updated is not None
    assert updated.enabled is False
    assert updated.period_value == 2


@pytest.mark.usefixtures("_check_db_available")
async def test_delete_schedule(db: TimescaleDBDatabase) -> None:
    """Schedule silme — tasks'daki schedule_id SET NULL olmalı."""
    cid = await _ensure_checklist(db)
    iid = await _ensure_asset_instance(db)
    if iid is None:
        pytest.skip("asset instance yok")

    created = await db.insert_maintenance_schedule(
        MaintenanceSchedule(
            checklist_id=cid,
            asset_instance_id=iid,
            period_kind="daily",
            anchor_date=date(2026, 5, 1),
            next_due_at=datetime(2026, 5, 1, tzinfo=UTC),
        ),
    )
    assert created.id is not None
    assert await db.delete_maintenance_schedule(created.id) is True
    assert await db.get_maintenance_schedule(created.id) is None
    assert await db.delete_maintenance_schedule(999999) is False


@pytest.mark.usefixtures("_check_db_available")
async def test_list_due_schedules(db: TimescaleDBDatabase) -> None:
    """Vadesi geçmiş enabled schedule'lar listelenmeli."""
    cid = await _ensure_checklist(db)
    iid = await _ensure_asset_instance(db)
    if iid is None:
        pytest.skip("asset instance yok")

    past = datetime.now(UTC) - timedelta(hours=1)
    future = datetime.now(UTC) + timedelta(hours=1)

    due = await db.insert_maintenance_schedule(
        MaintenanceSchedule(
            checklist_id=cid,
            asset_instance_id=iid,
            period_kind="daily",
            anchor_date=date(2026, 5, 1),
            next_due_at=past,
        ),
    )
    not_yet = await db.insert_maintenance_schedule(
        MaintenanceSchedule(
            checklist_id=cid,
            asset_instance_id=iid,
            period_kind="daily",
            anchor_date=date(2026, 5, 1),
            next_due_at=future,
        ),
    )

    result = await db.list_due_maintenance_schedules(datetime.now(UTC))
    result_ids = {s.id for s in result}
    assert due.id in result_ids
    assert not_yet.id not in result_ids


@pytest.mark.usefixtures("_check_db_available")
async def test_list_schedules_filter_by_enabled(
    db: TimescaleDBDatabase,
) -> None:
    """enabled filtresi çalışır."""
    cid = await _ensure_checklist(db)
    iid = await _ensure_asset_instance(db)
    if iid is None:
        pytest.skip("asset instance yok")

    s1 = await db.insert_maintenance_schedule(
        MaintenanceSchedule(
            checklist_id=cid,
            asset_instance_id=iid,
            period_kind="daily",
            anchor_date=date(2026, 5, 1),
            next_due_at=datetime(2026, 5, 1, tzinfo=UTC),
            enabled=True,
        ),
    )
    s2 = await db.insert_maintenance_schedule(
        MaintenanceSchedule(
            checklist_id=cid,
            asset_instance_id=iid,
            period_kind="daily",
            anchor_date=date(2026, 5, 1),
            next_due_at=datetime(2026, 5, 1, tzinfo=UTC),
            enabled=False,
        ),
    )

    enabled = await db.list_maintenance_schedules(enabled=True)
    enabled_ids = {s.id for s in enabled}
    assert s1.id in enabled_ids
    assert s2.id not in enabled_ids
