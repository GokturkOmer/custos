"""Threshold (eşik alarm tanımı) tablosunu oluştur.

Her tag için bir veya birden fazla alarm eşiği tanımlanabilir.
Yön (high/low), debounce ve hysteresis ISA-18.2 alarm
state machine'in temelini oluşturur.

Revision ID: 011
Revises: 010
Create Date: 2026-04-11
"""

from collections.abc import Sequence

from alembic import op

revision: str = "011"
down_revision: str | None = "010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """thresholds tablosunu oluştur."""
    op.execute(
        """
        CREATE TABLE thresholds (
            id SERIAL PRIMARY KEY,
            tag_id TEXT NOT NULL REFERENCES tags(tag_id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            direction TEXT NOT NULL DEFAULT 'high',
            set_point DOUBLE PRECISION NOT NULL,
            severity TEXT NOT NULL DEFAULT 'warn',
            debounce_seconds INTEGER NOT NULL DEFAULT 5,
            hysteresis DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(tag_id, name)
        );
        """
    )


def downgrade() -> None:
    """thresholds tablosunu kaldır."""
    op.execute("DROP TABLE thresholds;")
