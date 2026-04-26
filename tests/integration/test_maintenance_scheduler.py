"""MaintenanceScheduler integration testi.

Gerçek DB ile scheduler'ın tick'inde task ürettiği + next_due_at'i
ilerlettiği doğrulanır. Zamanı 'mocklamak' yerine past/future
timestamp'ler ile geçmiş/gelecek senaryoları kurulur.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest

from custos.analytics.maintenance_scheduler import MaintenanceScheduler
from custos.shared.database import (
    MaintenanceChecklist,
    MaintenanceChecklistStep,
    MaintenanceSchedule,
    TimescaleDBDatabase,
)


async def _make_checklist(db: TimescaleDBDatabase) -> int:
    """Scheduler testi için checklist oluşturur."""
    unique = uuid.uuid4().hex[:8]
    c = await db.insert_maintenance_checklist(
        MaintenanceChecklist(
            slug=f"test-sch-{unique}",
            title="Scheduler Test CL",
            category="periodic",
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


@pytest.mark.usefixtures("_check_db_available")
async def test_scheduler_tick_creates_task_for_due_schedule(
    db: TimescaleDBDatabase,
) -> None:
    """Vadesi geçmiş schedule → pending task üretilir, next_due_at ilerler."""
    cid = await _make_checklist(db)
    instances = await db.list_asset_instances()
    if not instances:
        pytest.skip("asset instance yok — scheduler testi atlanıyor")
    iid = instances[0].id
    assert iid is not None

    past_due = datetime.now(UTC) - timedelta(hours=1)
    sched = await db.insert_maintenance_schedule(
        MaintenanceSchedule(
            checklist_id=cid,
            asset_instance_id=iid,
            period_kind="daily",
            period_value=1,
            anchor_date=date.today(),
            next_due_at=past_due,
        ),
    )
    assert sched.id is not None

    scheduler = MaintenanceScheduler(db=db, tick_seconds=300)
    await scheduler.run_once()

    # Task üretildi mi?
    tasks = await db.list_maintenance_tasks_for_schedule(sched.id)
    assert len(tasks) >= 1
    assert tasks[0].source == "schedule"
    assert tasks[0].status == "pending"

    # next_due_at 1 gün ileriye alındı mı?
    refreshed = await db.get_maintenance_schedule(sched.id)
    assert refreshed is not None
    assert refreshed.next_due_at > past_due
    assert (refreshed.next_due_at - past_due) >= timedelta(days=1) - timedelta(minutes=1)


@pytest.mark.usefixtures("_check_db_available")
async def test_scheduler_tick_skips_future_schedule(
    db: TimescaleDBDatabase,
) -> None:
    """next_due_at henüz gelmemişse task üretilmez."""
    cid = await _make_checklist(db)
    instances = await db.list_asset_instances()
    if not instances:
        pytest.skip("asset instance yok")
    iid = instances[0].id
    assert iid is not None

    future = datetime.now(UTC) + timedelta(hours=6)
    sched = await db.insert_maintenance_schedule(
        MaintenanceSchedule(
            checklist_id=cid,
            asset_instance_id=iid,
            period_kind="weekly",
            anchor_date=date.today(),
            next_due_at=future,
        ),
    )
    assert sched.id is not None

    scheduler = MaintenanceScheduler(db=db, tick_seconds=300)
    await scheduler.run_once()

    tasks = await db.list_maintenance_tasks_for_schedule(sched.id)
    assert len(tasks) == 0


@pytest.mark.usefixtures("_check_db_available")
async def test_scheduler_tick_skips_disabled_schedule(
    db: TimescaleDBDatabase,
) -> None:
    """enabled=False schedule tick'te atlanır."""
    cid = await _make_checklist(db)
    instances = await db.list_asset_instances()
    if not instances:
        pytest.skip("asset instance yok")
    iid = instances[0].id
    assert iid is not None

    past = datetime.now(UTC) - timedelta(hours=1)
    sched = await db.insert_maintenance_schedule(
        MaintenanceSchedule(
            checklist_id=cid,
            asset_instance_id=iid,
            period_kind="daily",
            anchor_date=date.today(),
            next_due_at=past,
            enabled=False,
        ),
    )
    assert sched.id is not None

    scheduler = MaintenanceScheduler(db=db, tick_seconds=300)
    await scheduler.run_once()

    tasks = await db.list_maintenance_tasks_for_schedule(sched.id)
    assert len(tasks) == 0
