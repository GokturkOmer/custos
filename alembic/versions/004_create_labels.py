"""Etiket tablosunu oluştur.

Brief bölüm 8 — Veri katmanı 3. Anomali ve olay etiketlerinin saklandığı tablo.

Revision ID: 004
Revises: 003
Create Date: 2026-04-08
"""

from collections.abc import Sequence

from alembic import op

revision: str = "004"
down_revision: str | None = "003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """labels tablosunu oluştur."""
    op.execute(
        """
        CREATE TABLE labels (
            id BIGSERIAL PRIMARY KEY,
            timestamp_start TIMESTAMPTZ NOT NULL,
            timestamp_end TIMESTAMPTZ NOT NULL,
            event_type TEXT NOT NULL,
            confidence TEXT NOT NULL,
            source TEXT NOT NULL,
            notes TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )
    op.execute("CREATE INDEX idx_labels_event_type ON labels (event_type);")
    op.execute(
        """
        CREATE INDEX idx_labels_time_range
            ON labels (timestamp_start, timestamp_end);
        """
    )


def downgrade() -> None:
    """labels tablosunu kaldır."""
    op.execute("DROP TABLE labels;")
