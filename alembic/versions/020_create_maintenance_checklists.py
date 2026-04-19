"""Maintenance checklist (kontrol listesi) tablolarını oluştur.

İki tablo birlikte:
- maintenance_checklists: başlık, kategori, opsiyonel asset template bağı
- maintenance_checklist_steps: her checklist'in sıralı adımları

Checklist ya periyodik bakımdan (schedule) ya da alarmdan çağrılır.
Kategori alanı (periodic / alarm / generic) UI filtrelemesi için.

Revision ID: 020
Revises: 019
Create Date: 2026-04-19
"""

from collections.abc import Sequence

from alembic import op

revision: str = "020"
down_revision: str | None = "019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """maintenance_checklists + maintenance_checklist_steps tablolarını oluştur."""
    op.execute(
        """
        CREATE TABLE maintenance_checklists (
            id SERIAL PRIMARY KEY,
            slug TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            category TEXT NOT NULL DEFAULT 'generic',
            asset_template_id INTEGER REFERENCES asset_templates(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CHECK (category IN ('periodic', 'alarm', 'generic'))
        );

        CREATE INDEX idx_maint_checklist_category
            ON maintenance_checklists (category);
        CREATE INDEX idx_maint_checklist_template
            ON maintenance_checklists (asset_template_id)
            WHERE asset_template_id IS NOT NULL;

        CREATE TABLE maintenance_checklist_steps (
            id SERIAL PRIMARY KEY,
            checklist_id INTEGER NOT NULL
                REFERENCES maintenance_checklists(id) ON DELETE CASCADE,
            sort_order INTEGER NOT NULL DEFAULT 0,
            text TEXT NOT NULL,
            estimated_minutes INTEGER,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE INDEX idx_maint_checklist_steps_checklist
            ON maintenance_checklist_steps (checklist_id, sort_order);
        """
    )


def downgrade() -> None:
    """maintenance_checklist_steps + maintenance_checklists tablolarını kaldır."""
    op.execute(
        """
        DROP TABLE maintenance_checklist_steps;
        DROP TABLE maintenance_checklists;
        """
    )
