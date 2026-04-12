"""Overview grafik tag konfigurasyon tablosunu olustur.

Her grafik slotu icin hangi tag'larin gosterilecegini saklar.
4 sabit slot: temp_chart, pressure_chart, flow_vibration_chart, rpm_chart.

Revision ID: 017
Revises: 016
Create Date: 2026-04-12
"""

from collections.abc import Sequence

from alembic import op

revision: str = "017"
down_revision: str | None = "016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """overview_chart_tags tablosunu olustur."""
    op.execute(
        """
        CREATE TABLE overview_chart_tags (
            id SERIAL PRIMARY KEY,
            chart_key TEXT NOT NULL,
            tag_id TEXT NOT NULL REFERENCES tags(tag_id) ON DELETE CASCADE,
            sort_order INT NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(chart_key, tag_id)
        );
        """
    )


def downgrade() -> None:
    """overview_chart_tags tablosunu kaldir."""
    op.execute("DROP TABLE overview_chart_tags;")
