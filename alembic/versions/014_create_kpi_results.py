"""KPI hesaplama sonuçları tablosunu oluştur.

Asset instance'lar için hesaplanan KPI değerlerini
1 dakikalık bucket'lar halinde saklar.

Revision ID: 014
Revises: 013
Create Date: 2026-04-11
"""

from collections.abc import Sequence

from alembic import op

revision: str = "014"
down_revision: str | None = "013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """kpi_results tablosunu oluştur."""
    op.execute(
        """
        CREATE TABLE kpi_results (
            id BIGSERIAL PRIMARY KEY,
            instance_id INTEGER NOT NULL REFERENCES asset_instances(id) ON DELETE CASCADE,
            kpi_definition_id INTEGER NOT NULL REFERENCES kpi_definitions(id) ON DELETE CASCADE,
            bucket_start TIMESTAMPTZ NOT NULL,
            value DOUBLE PRECISION NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(instance_id, kpi_definition_id, bucket_start)
        );

        CREATE INDEX idx_kpi_results_instance
            ON kpi_results (instance_id, bucket_start DESC);
        CREATE INDEX idx_kpi_results_definition
            ON kpi_results (kpi_definition_id, bucket_start DESC);
        """
    )


def downgrade() -> None:
    """kpi_results tablosunu kaldır."""
    op.execute("DROP TABLE kpi_results;")
