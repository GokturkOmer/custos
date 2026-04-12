"""Anomali skor tablosunu oluştur.

Asset instance'lar için Isolation Forest anomali skorlarını saklar.

Revision ID: 015
Revises: 014
Create Date: 2026-04-11
"""

from collections.abc import Sequence

from alembic import op

revision: str = "015"
down_revision: str | None = "014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """anomaly_scores tablosunu oluştur."""
    op.execute(
        """
        CREATE TABLE anomaly_scores (
            id BIGSERIAL PRIMARY KEY,
            instance_id INTEGER NOT NULL REFERENCES asset_instances(id) ON DELETE CASCADE,
            timestamp TIMESTAMPTZ NOT NULL,
            score DOUBLE PRECISION NOT NULL,
            is_anomaly BOOLEAN NOT NULL DEFAULT FALSE,
            feature_vector TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE INDEX idx_anomaly_scores_instance
            ON anomaly_scores (instance_id, timestamp DESC);
        CREATE INDEX idx_anomaly_scores_anomaly
            ON anomaly_scores (is_anomaly, timestamp DESC);
        """
    )


def downgrade() -> None:
    """anomaly_scores tablosunu kaldır."""
    op.execute("DROP TABLE anomaly_scores;")
