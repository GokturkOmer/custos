"""Maintenance schedule (periyodik bakım takvimi) tablosunu oluştur.

Her schedule bir checklist ile bir asset (template veya instance) arasında
periyodik tetikleme tanımlar. Scheduler servisi next_due_at'i kontrol
ederek maintenance_tasks tablosuna kayıt açar.

CHECK constraint: asset_template_id veya asset_instance_id'den TAM OLARAK
biri dolu olmalı (XOR). Brief §4.8: bakım ya template bazlı (tüm
instance'lara uygulanır) ya instance bazlıdır.

Revision ID: 021
Revises: 020
Create Date: 2026-04-19
"""

from collections.abc import Sequence

from alembic import op

revision: str = "021"
down_revision: str | None = "020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """maintenance_schedules tablosunu oluştur."""
    op.execute(
        """
        CREATE TABLE maintenance_schedules (
            id SERIAL PRIMARY KEY,
            checklist_id INTEGER NOT NULL
                REFERENCES maintenance_checklists(id) ON DELETE CASCADE,
            asset_template_id INTEGER
                REFERENCES asset_templates(id) ON DELETE CASCADE,
            asset_instance_id INTEGER
                REFERENCES asset_instances(id) ON DELETE CASCADE,
            period_kind TEXT NOT NULL,
            period_value INTEGER NOT NULL DEFAULT 1,
            anchor_date DATE NOT NULL,
            next_due_at TIMESTAMPTZ NOT NULL,
            notify_lead_hours INTEGER NOT NULL DEFAULT 24,
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CHECK (period_kind IN ('daily', 'weekly', 'monthly', 'yearly', 'custom_days')),
            CHECK (period_value >= 1),
            CHECK (
                (asset_template_id IS NOT NULL AND asset_instance_id IS NULL)
                OR (asset_template_id IS NULL AND asset_instance_id IS NOT NULL)
            )
        );

        CREATE INDEX idx_maint_schedule_due
            ON maintenance_schedules (next_due_at)
            WHERE enabled = TRUE;
        CREATE INDEX idx_maint_schedule_checklist
            ON maintenance_schedules (checklist_id);
        """
    )


def downgrade() -> None:
    """maintenance_schedules tablosunu kaldır."""
    op.execute("DROP TABLE maintenance_schedules;")
