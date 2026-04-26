"""Alarm → checklist eşleme + recent alarm count entegrasyon testleri.

TimescaleDB'nin ayakta olmasını gerektirir.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from custos.shared.database import (
    AlarmEvent,
    MaintenanceChecklist,
    MaintenanceChecklistStep,
    TagRecord,
    Threshold,
    TimescaleDBDatabase,
)


async def _setup_threshold_and_checklist(
    db: TimescaleDBDatabase,
) -> tuple[int, int]:
    """Threshold + checklist oluşturup id'lerini döndürür."""
    unique = uuid.uuid4().hex[:8]
    tag_id = f"TEST_{unique}"
    await db.insert_tag(
        TagRecord(
            tag_id=tag_id,
            name="Test Tag",
            modbus_host="127.0.0.1",
            register_address=40001,
        ),
    )
    th = await db.insert_threshold(
        Threshold(tag_id=tag_id, name="High", set_point=50.0),
    )
    assert th.id is not None
    cl = await db.insert_maintenance_checklist(
        MaintenanceChecklist(
            slug=f"test-map-{unique}",
            title="Mapping CL",
            category="alarm",
            steps=[
                MaintenanceChecklistStep(
                    checklist_id=0,
                    sort_order=0,
                    text="Adım",
                    estimated_minutes=2,
                ),
            ],
        ),
    )
    assert cl.id is not None
    return th.id, cl.id


@pytest.mark.usefixtures("_check_db_available")
async def test_upsert_mapping(db: TimescaleDBDatabase) -> None:
    """Yeni mapping ekle — geri okunabilir."""
    th_id, cl_id = await _setup_threshold_and_checklist(db)
    mapping = await db.upsert_alarm_checklist_mapping(th_id, cl_id)
    assert mapping.id is not None
    assert mapping.threshold_id == th_id
    assert mapping.checklist_id == cl_id


@pytest.mark.usefixtures("_check_db_available")
async def test_upsert_mapping_overwrites(db: TimescaleDBDatabase) -> None:
    """Aynı threshold için ikinci upsert eski kaydı günceller (1:1)."""
    th_id, cl_id1 = await _setup_threshold_and_checklist(db)
    # İkinci checklist
    unique = uuid.uuid4().hex[:8]
    cl2 = await db.insert_maintenance_checklist(
        MaintenanceChecklist(
            slug=f"test-map2-{unique}",
            title="Second CL",
            steps=[
                MaintenanceChecklistStep(
                    checklist_id=0,
                    sort_order=0,
                    text="X",
                    estimated_minutes=1,
                ),
            ],
        ),
    )
    assert cl2.id is not None

    first = await db.upsert_alarm_checklist_mapping(th_id, cl_id1)
    second = await db.upsert_alarm_checklist_mapping(th_id, cl2.id)
    assert first.id == second.id  # aynı row, overwrite
    assert second.checklist_id == cl2.id

    # list tek kayıt döndürmeli (UNIQUE threshold_id)
    all_mappings = await db.list_alarm_checklist_mappings()
    matching = [m for m in all_mappings if m.threshold_id == th_id]
    assert len(matching) == 1


@pytest.mark.usefixtures("_check_db_available")
async def test_delete_mapping(db: TimescaleDBDatabase) -> None:
    """Mapping silme."""
    th_id, cl_id = await _setup_threshold_and_checklist(db)
    await db.upsert_alarm_checklist_mapping(th_id, cl_id)
    assert await db.delete_alarm_checklist_mapping(th_id) is True
    assert await db.get_alarm_checklist_mapping(th_id) is None
    assert await db.delete_alarm_checklist_mapping(999999) is False


@pytest.mark.usefixtures("_check_db_available")
async def test_count_alarm_events_for_threshold(
    db: TimescaleDBDatabase,
) -> None:
    """Bir threshold'un belirli tarihten sonra tetiklenme sayısı."""
    th_id, _ = await _setup_threshold_and_checklist(db)

    now = datetime.now(UTC)
    await db.insert_alarm_event(
        AlarmEvent(
            threshold_id=th_id,
            tag_id="TEST_count",
            triggered_at=now - timedelta(days=3),
            trigger_value=55.0,
        ),
    )
    await db.insert_alarm_event(
        AlarmEvent(
            threshold_id=th_id,
            tag_id="TEST_count",
            triggered_at=now - timedelta(days=10),
            trigger_value=60.0,
        ),
    )

    # Son 7 gün — 1 alarm
    count_7d = await db.count_alarm_events_for_threshold(
        th_id,
        now - timedelta(days=7),
    )
    assert count_7d == 1

    # Son 30 gün — 2 alarm
    count_30d = await db.count_alarm_events_for_threshold(
        th_id,
        now - timedelta(days=30),
    )
    assert count_30d == 2
