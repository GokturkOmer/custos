"""Alarm → checklist eşleme tablosunu oluştur.

Her threshold opsiyonel olarak bir checklist'e bağlanabilir (1:1).
Alarm tetiklendiğinde bu eşleme üzerinden operatöre "Kontrol Listesi Başlat"
aksiyonu sunulur; tıklayınca maintenance_task (source='alarm') açılır.

Brief §4.8 kararı: threshold bazlı basit eşleme. Daha karmaşık (severity
bazlı, asset template bazlı generic) eşlemeler v1.1 backlog'da.

Revision ID: 023
Revises: 022
Create Date: 2026-04-19
"""

from collections.abc import Sequence

from alembic import op

revision: str = "023"
down_revision: str | None = "022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """alarm_checklist_mappings tablosunu oluştur."""
    op.execute(
        """
        CREATE TABLE alarm_checklist_mappings (
            id SERIAL PRIMARY KEY,
            threshold_id INTEGER NOT NULL UNIQUE
                REFERENCES thresholds(id) ON DELETE CASCADE,
            checklist_id INTEGER NOT NULL
                REFERENCES maintenance_checklists(id) ON DELETE RESTRICT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE INDEX idx_alarm_checklist_checklist
            ON alarm_checklist_mappings (checklist_id);
        """
    )


def downgrade() -> None:
    """alarm_checklist_mappings tablosunu kaldır."""
    op.execute("DROP TABLE alarm_checklist_mappings;")
