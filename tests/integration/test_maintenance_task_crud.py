"""Maintenance task + step result CRUD entegrasyon testleri.

TimescaleDB'nin ayakta olmasını gerektirir.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from custos.shared.database import (
    MaintenanceChecklist,
    MaintenanceChecklistStep,
    MaintenanceTask,
    MaintenanceTaskStepResult,
    TimescaleDBDatabase,
)


async def _ensure_checklist_with_steps(
    db: TimescaleDBDatabase,
) -> MaintenanceChecklist:
    """Task'a bağlamak için checklist + steps oluştur."""
    unique = uuid.uuid4().hex[:8]
    c = MaintenanceChecklist(
        slug=f"test-task-{unique}",
        title="Test Task CL",
        steps=[
            MaintenanceChecklistStep(
                checklist_id=0, sort_order=0,
                text="Adım 1", estimated_minutes=5,
            ),
            MaintenanceChecklistStep(
                checklist_id=0, sort_order=1,
                text="Adım 2", estimated_minutes=5,
            ),
        ],
    )
    created = await db.insert_maintenance_checklist(c)
    assert created.id is not None
    return created


@pytest.mark.usefixtures("_check_db_available")
async def test_insert_task_from_manual(db: TimescaleDBDatabase) -> None:
    """Manuel kaynaklı task — minimum alanlarla oluşturma."""
    cl = await _ensure_checklist_with_steps(db)
    assert cl.id is not None

    task = MaintenanceTask(
        checklist_id=cl.id,
        source="manual",
        title_snapshot=cl.title,
    )
    created = await db.insert_maintenance_task(task)
    assert created.id is not None
    assert created.source == "manual"
    assert created.status == "pending"


@pytest.mark.usefixtures("_check_db_available")
async def test_update_task_to_completed(db: TimescaleDBDatabase) -> None:
    """Task status güncelleme (completed_at da set et)."""
    cl = await _ensure_checklist_with_steps(db)
    assert cl.id is not None
    created = await db.insert_maintenance_task(
        MaintenanceTask(
            checklist_id=cl.id, source="manual",
            title_snapshot=cl.title,
        ),
    )
    assert created.id is not None

    now = datetime.now(UTC)
    updated = await db.update_maintenance_task(
        created.id,
        {"status": "completed", "completed_at": now, "completed_by": "tester"},
    )
    assert updated is not None
    assert updated.status == "completed"
    assert updated.completed_at is not None
    assert updated.completed_by == "tester"


@pytest.mark.usefixtures("_check_db_available")
async def test_list_upcoming_maintenance_tasks(
    db: TimescaleDBDatabase,
) -> None:
    """within_hours içindeki pending/in_progress task'lar döner."""
    cl = await _ensure_checklist_with_steps(db)
    assert cl.id is not None

    # Due: 1 saat sonra — upcoming
    near = await db.insert_maintenance_task(
        MaintenanceTask(
            checklist_id=cl.id, source="schedule",
            title_snapshot=cl.title,
            due_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    # Due: 200 saat sonra — upcoming (48h içinde değil)
    far = await db.insert_maintenance_task(
        MaintenanceTask(
            checklist_id=cl.id, source="schedule",
            title_snapshot=cl.title,
            due_at=datetime.now(UTC) + timedelta(hours=200),
        ),
    )

    upcoming = await db.list_upcoming_maintenance_tasks(within_hours=48)
    upcoming_ids = {t.id for t in upcoming}
    assert near.id in upcoming_ids
    assert far.id not in upcoming_ids


@pytest.mark.usefixtures("_check_db_available")
async def test_list_recent_maintenance_tasks(
    db: TimescaleDBDatabase,
) -> None:
    """Tamamlanmış task'lar geçmiş listesinde görünür."""
    cl = await _ensure_checklist_with_steps(db)
    assert cl.id is not None

    task = await db.insert_maintenance_task(
        MaintenanceTask(
            checklist_id=cl.id, source="manual",
            title_snapshot=cl.title,
        ),
    )
    assert task.id is not None
    now = datetime.now(UTC)
    await db.update_maintenance_task(
        task.id,
        {"status": "completed", "completed_at": now},
    )

    recent = await db.list_recent_maintenance_tasks(limit=100)
    recent_ids = {t.id for t in recent}
    assert task.id in recent_ids


@pytest.mark.usefixtures("_check_db_available")
async def test_upsert_step_result(db: TimescaleDBDatabase) -> None:
    """Adım sonucu upsert — ikinci çağrıda günceller, eklemez."""
    cl = await _ensure_checklist_with_steps(db)
    assert cl.id is not None
    assert cl.steps[0].id is not None

    task = await db.insert_maintenance_task(
        MaintenanceTask(
            checklist_id=cl.id, source="manual",
            title_snapshot=cl.title,
        ),
    )
    assert task.id is not None

    first = await db.upsert_maintenance_task_step_result(
        MaintenanceTaskStepResult(
            task_id=task.id, step_id=cl.steps[0].id,
            checked=True, note="ilk not",
        ),
    )
    assert first.id is not None
    assert first.checked is True

    # Aynı (task, step) — upsert güncellemeli
    second = await db.upsert_maintenance_task_step_result(
        MaintenanceTaskStepResult(
            task_id=task.id, step_id=cl.steps[0].id,
            checked=False, note="ikinci not",
        ),
    )
    assert second.id == first.id
    assert second.checked is False
    assert second.note == "ikinci not"

    # Liste 1 kayıt döndürmeli
    results = await db.list_maintenance_task_step_results(task.id)
    assert len(results) == 1


@pytest.mark.usefixtures("_check_db_available")
async def test_update_task_rejects_invalid_fields(
    db: TimescaleDBDatabase,
) -> None:
    """Bilinmeyen alan güncellenirse ValueError."""
    cl = await _ensure_checklist_with_steps(db)
    assert cl.id is not None
    task = await db.insert_maintenance_task(
        MaintenanceTask(
            checklist_id=cl.id, source="manual", title_snapshot=cl.title,
        ),
    )
    assert task.id is not None

    with pytest.raises(ValueError, match="Güncellenemeyen alanlar"):
        await db.update_maintenance_task(task.id, {"source": "alarm"})
