"""Alarm event tablosunu oluştur.

ISA-18.2 alarm state machine event'lerini saklar.
Her alarm: triggered → acknowledged → cleared durumlarından geçer.

Revision ID: 012
Revises: 011
Create Date: 2026-04-11
"""

from collections.abc import Sequence

from alembic import op

revision: str = "012"
down_revision: str | None = "011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """alarm_events tablosunu oluştur."""
    op.execute(
        """
        CREATE TABLE alarm_events (
            id BIGSERIAL PRIMARY KEY,
            threshold_id INTEGER NOT NULL REFERENCES thresholds(id) ON DELETE CASCADE,
            tag_id TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'triggered',
            triggered_at TIMESTAMPTZ NOT NULL,
            acknowledged_at TIMESTAMPTZ,
            cleared_at TIMESTAMPTZ,
            trigger_value DOUBLE PRECISION NOT NULL,
            clear_value DOUBLE PRECISION,
            notes TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE INDEX idx_alarm_events_state ON alarm_events (state);
        CREATE INDEX idx_alarm_events_tag ON alarm_events (tag_id, triggered_at DESC);
        CREATE INDEX idx_alarm_events_time ON alarm_events (triggered_at DESC);
        """
    )


def downgrade() -> None:
    """alarm_events tablosunu kaldır."""
    op.execute("DROP TABLE alarm_events;")
