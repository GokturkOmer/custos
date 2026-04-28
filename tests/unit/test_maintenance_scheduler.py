"""PP-08 — MaintenanceScheduler unit testleri.

run_once / _process_due_schedule / _mark_overdue_as_missed branch'lerini
tek tek mock'lu DB ile doğrular. Real ``compute_next_due_at`` kullanır
(maintenance_periods kendi unit testleriyle yeterince kapsanmış).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from custos.analytics.maintenance_scheduler import (
    MISSED_THRESHOLD_HOURS,
    MaintenanceScheduler,
)
from custos.shared.database import (
    AssetInstance,
    MaintenanceChecklist,
    MaintenanceSchedule,
    MaintenanceTask,
)


def _schedule(
    *,
    sid: int | None = 1,
    asset_instance_id: int | None = None,
    asset_template_id: int | None = None,
) -> MaintenanceSchedule:
    s = MaintenanceSchedule(
        checklist_id=1,
        period_kind="weekly",
        anchor_date=date(2026, 1, 1),
        next_due_at=datetime(2026, 4, 28, 12, 0, tzinfo=UTC),
        asset_template_id=asset_template_id,
        asset_instance_id=asset_instance_id,
    )
    s.id = sid
    return s


def _checklist(cid: int = 1) -> MaintenanceChecklist:
    c = MaintenanceChecklist(slug="filtre", title="Filtre Kontrolü")
    c.id = cid
    return c


def _instance(iid: int) -> AssetInstance:
    inst = AssetInstance(template_id=1, name=f"asset-{iid}")
    inst.id = iid
    return inst


@pytest.mark.asyncio
async def test_run_once_with_no_due_schedules_does_nothing() -> None:
    """Boş due liste + boş upcoming → hiç insert/update yok."""
    db = MagicMock()
    db.list_due_maintenance_schedules = AsyncMock(return_value=[])
    db.list_upcoming_maintenance_tasks = AsyncMock(return_value=[])
    db.insert_maintenance_task = AsyncMock()

    scheduler = MaintenanceScheduler(db=db)
    await scheduler.run_once()

    db.list_due_maintenance_schedules.assert_awaited_once()
    db.list_upcoming_maintenance_tasks.assert_awaited_once()
    db.insert_maintenance_task.assert_not_called()


@pytest.mark.asyncio
async def test_process_due_schedule_skips_when_id_none() -> None:
    """sched.id None ise hiçbir DB sorgusu yapılmaz."""
    db = MagicMock()
    db.get_maintenance_checklist = AsyncMock()
    sched = _schedule(sid=None)

    scheduler = MaintenanceScheduler(db=db)
    await scheduler._process_due_schedule(sched, datetime.now(UTC))

    db.get_maintenance_checklist.assert_not_called()


@pytest.mark.asyncio
async def test_process_due_schedule_skips_when_checklist_missing() -> None:
    """Checklist bulunamazsa task üretilmez."""
    db = MagicMock()
    db.get_maintenance_checklist = AsyncMock(return_value=None)
    db.insert_maintenance_task = AsyncMock()
    db.update_maintenance_schedule = AsyncMock()

    scheduler = MaintenanceScheduler(db=db)
    await scheduler._process_due_schedule(_schedule(), datetime.now(UTC))

    db.insert_maintenance_task.assert_not_called()
    db.update_maintenance_schedule.assert_not_called()


@pytest.mark.asyncio
async def test_process_due_schedule_single_instance_creates_one_task() -> None:
    """asset_instance_id varsa tek task üretilir + next_due_at ilerletilir."""
    db = MagicMock()
    db.get_maintenance_checklist = AsyncMock(return_value=_checklist())
    db.insert_maintenance_task = AsyncMock()
    db.update_maintenance_schedule = AsyncMock()

    scheduler = MaintenanceScheduler(db=db)
    sched = _schedule(asset_instance_id=42)
    await scheduler._process_due_schedule(sched, datetime.now(UTC))

    assert db.insert_maintenance_task.await_count == 1
    task = db.insert_maintenance_task.await_args.args[0]
    assert task.asset_instance_id == 42
    assert task.source == "schedule"
    assert task.status == "pending"
    db.update_maintenance_schedule.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_due_schedule_template_creates_task_per_instance() -> None:
    """asset_template_id'li schedule template'in tüm instance'larına task açar."""
    db = MagicMock()
    db.get_maintenance_checklist = AsyncMock(return_value=_checklist())
    db.list_asset_instances = AsyncMock(
        return_value=[_instance(10), _instance(20), _instance(30)]
    )
    db.insert_maintenance_task = AsyncMock()
    db.update_maintenance_schedule = AsyncMock()

    scheduler = MaintenanceScheduler(db=db)
    sched = _schedule(asset_template_id=5)
    await scheduler._process_due_schedule(sched, datetime.now(UTC))

    assert db.insert_maintenance_task.await_count == 3
    iids = [c.args[0].asset_instance_id for c in db.insert_maintenance_task.await_args_list]
    assert iids == [10, 20, 30]


@pytest.mark.asyncio
async def test_process_due_schedule_template_without_instances_falls_back_to_none() -> None:
    """asset_template_id var ama instance yoksa tek None-instance task üretir."""
    db = MagicMock()
    db.get_maintenance_checklist = AsyncMock(return_value=_checklist())
    db.list_asset_instances = AsyncMock(return_value=[])
    db.insert_maintenance_task = AsyncMock()
    db.update_maintenance_schedule = AsyncMock()

    scheduler = MaintenanceScheduler(db=db)
    sched = _schedule(asset_template_id=5)
    await scheduler._process_due_schedule(sched, datetime.now(UTC))

    assert db.insert_maintenance_task.await_count == 1
    task = db.insert_maintenance_task.await_args.args[0]
    assert task.asset_instance_id is None


@pytest.mark.asyncio
async def test_process_due_schedule_no_target_creates_orphan_task() -> None:
    """Ne instance ne template — yine tek task None-instance ile üretir."""
    db = MagicMock()
    db.get_maintenance_checklist = AsyncMock(return_value=_checklist())
    db.insert_maintenance_task = AsyncMock()
    db.update_maintenance_schedule = AsyncMock()

    scheduler = MaintenanceScheduler(db=db)
    await scheduler._process_due_schedule(_schedule(), datetime.now(UTC))

    assert db.insert_maintenance_task.await_count == 1
    task = db.insert_maintenance_task.await_args.args[0]
    assert task.asset_instance_id is None


@pytest.mark.asyncio
async def test_mark_overdue_marks_old_pending_as_missed() -> None:
    """due_at < now - threshold + status=pending → 'missed' güncellenir."""
    now = datetime.now(UTC)
    too_old = now - timedelta(hours=MISSED_THRESHOLD_HOURS + 1)
    fresh = now - timedelta(hours=1)

    def _task(tid: int, due_at: datetime, status: str) -> MaintenanceTask:
        t = MaintenanceTask(
            checklist_id=1,
            source="schedule",
            title_snapshot="x",
            due_at=due_at,
            status=status,
        )
        t.id = tid
        return t

    db = MagicMock()
    db.list_upcoming_maintenance_tasks = AsyncMock(
        return_value=[
            _task(100, too_old, "pending"),
            _task(101, fresh, "pending"),
            _task(102, too_old, "completed"),
        ]
    )
    db.update_maintenance_task = AsyncMock()

    scheduler = MaintenanceScheduler(db=db)
    await scheduler._mark_overdue_as_missed(now)

    # Sadece 100 missed olur (101 fresh, 102 completed)
    assert db.update_maintenance_task.await_count == 1
    call_args = db.update_maintenance_task.await_args
    assert call_args.args[0] == 100
    assert call_args.args[1] == {"status": "missed"}


@pytest.mark.asyncio
async def test_mark_overdue_skips_task_with_none_due_at() -> None:
    """due_at None ise overdue hesabı yapılmaz."""
    now = datetime.now(UTC)
    t = MaintenanceTask(
        checklist_id=1,
        source="manual",
        title_snapshot="x",
        due_at=None,
        status="pending",
    )
    t.id = 200

    db = MagicMock()
    db.list_upcoming_maintenance_tasks = AsyncMock(return_value=[t])
    db.update_maintenance_task = AsyncMock()

    scheduler = MaintenanceScheduler(db=db)
    await scheduler._mark_overdue_as_missed(now)

    db.update_maintenance_task.assert_not_called()


@pytest.mark.asyncio
async def test_mark_overdue_skips_task_with_id_none() -> None:
    """task.id None ise update edilmez (defansif)."""
    now = datetime.now(UTC)
    too_old = now - timedelta(hours=MISSED_THRESHOLD_HOURS + 1)
    t = MaintenanceTask(
        checklist_id=1,
        source="schedule",
        title_snapshot="x",
        due_at=too_old,
        status="pending",
    )
    # id default None — ata değil

    db = MagicMock()
    db.list_upcoming_maintenance_tasks = AsyncMock(return_value=[t])
    db.update_maintenance_task = AsyncMock()

    scheduler = MaintenanceScheduler(db=db)
    await scheduler._mark_overdue_as_missed(now)

    db.update_maintenance_task.assert_not_called()
