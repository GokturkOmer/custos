"""Maintenance task + step result tablolarını oluştur.

- maintenance_tasks: tetiklenmiş bir bakım görevi (schedule/alarm/manual)
- maintenance_task_step_results: her checklist adımının tamamlanma durumu

Bir task açıldığında henüz step_result kaydı yoktur; operatör ilk kutucuğu
işaretlediğinde veya tüm checklist'i "tamamla" dediğinde satırlar yazılır.

Revision ID: 022
Revises: 021
Create Date: 2026-04-19
"""

from collections.abc import Sequence

from alembic import op

revision: str = "022"
down_revision: str | None = "021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """maintenance_tasks + maintenance_task_step_results tablolarını oluştur."""
    op.execute(
        """
        CREATE TABLE maintenance_tasks (
            id SERIAL PRIMARY KEY,
            schedule_id INTEGER
                REFERENCES maintenance_schedules(id) ON DELETE SET NULL,
            checklist_id INTEGER NOT NULL
                REFERENCES maintenance_checklists(id) ON DELETE RESTRICT,
            asset_instance_id INTEGER
                REFERENCES asset_instances(id) ON DELETE SET NULL,
            source TEXT NOT NULL,
            alarm_event_id BIGINT
                REFERENCES alarm_events(id) ON DELETE SET NULL,
            title_snapshot TEXT NOT NULL,
            due_at TIMESTAMPTZ,
            started_at TIMESTAMPTZ,
            completed_at TIMESTAMPTZ,
            completed_by TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CHECK (source IN ('schedule', 'alarm', 'manual')),
            CHECK (status IN (
                'pending', 'in_progress', 'completed', 'skipped', 'missed'
            ))
        );

        CREATE INDEX idx_maint_task_status_due
            ON maintenance_tasks (status, due_at);
        CREATE INDEX idx_maint_task_completed
            ON maintenance_tasks (completed_at DESC)
            WHERE completed_at IS NOT NULL;
        CREATE INDEX idx_maint_task_schedule
            ON maintenance_tasks (schedule_id)
            WHERE schedule_id IS NOT NULL;

        CREATE TABLE maintenance_task_step_results (
            id SERIAL PRIMARY KEY,
            task_id INTEGER NOT NULL
                REFERENCES maintenance_tasks(id) ON DELETE CASCADE,
            step_id INTEGER NOT NULL
                REFERENCES maintenance_checklist_steps(id) ON DELETE RESTRICT,
            checked BOOLEAN NOT NULL DEFAULT FALSE,
            note TEXT NOT NULL DEFAULT '',
            completed_at TIMESTAMPTZ,
            UNIQUE (task_id, step_id)
        );

        CREATE INDEX idx_maint_task_step_task
            ON maintenance_task_step_results (task_id);
        """
    )


def downgrade() -> None:
    """maintenance_task_step_results + maintenance_tasks tablolarını kaldır."""
    op.execute(
        """
        DROP TABLE maintenance_task_step_results;
        DROP TABLE maintenance_tasks;
        """
    )
