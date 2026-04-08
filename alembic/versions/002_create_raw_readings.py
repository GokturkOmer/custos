"""Sensör ham okumalarının saklandığı hypertable oluştur.

Brief bölüm 8 — Veri katmanı 1.

Revision ID: 002
Revises: 001
Create Date: 2026-04-08
"""

from collections.abc import Sequence

from alembic import op

revision: str = "002"
down_revision: str | None = "001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """raw_readings tablosunu ve hypertable'ını oluştur."""
    op.execute(
        """
        CREATE TABLE raw_readings (
            timestamp TIMESTAMPTZ NOT NULL,
            sensor_id TEXT NOT NULL,
            value DOUBLE PRECISION NOT NULL,
            quality_flag SMALLINT NOT NULL DEFAULT 0
        );
        """
    )
    op.execute("SELECT create_hypertable('raw_readings', 'timestamp');")
    op.execute(
        """
        CREATE INDEX idx_raw_readings_sensor_time
            ON raw_readings (sensor_id, timestamp DESC);
        """
    )


def downgrade() -> None:
    """raw_readings tablosunu kaldır."""
    op.execute("DROP TABLE raw_readings;")
