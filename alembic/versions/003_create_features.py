"""Hesaplanmış özelliklerin saklandığı hypertable oluştur.

Brief bölüm 8 — Veri katmanı 2.

Revision ID: 003
Revises: 002
Create Date: 2026-04-08
"""

from collections.abc import Sequence

from alembic import op

revision: str = "003"
down_revision: str | None = "002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """features tablosunu ve hypertable'ını oluştur."""
    op.execute(
        """
        CREATE TABLE features (
            timestamp TIMESTAMPTZ NOT NULL,
            sensor_id TEXT NOT NULL,
            feature_name TEXT NOT NULL,
            feature_value DOUBLE PRECISION NOT NULL,
            window_size_seconds INTEGER NOT NULL
        );
        """
    )
    op.execute("SELECT create_hypertable('features', 'timestamp');")
    op.execute(
        """
        CREATE INDEX idx_features_sensor_name_time
            ON features (sensor_id, feature_name, timestamp DESC);
        """
    )


def downgrade() -> None:
    """features tablosunu kaldır."""
    op.execute("DROP TABLE features;")
