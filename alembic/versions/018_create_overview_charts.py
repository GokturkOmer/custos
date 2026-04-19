"""Overview chart'larini dinamik hale getir.

overview_charts: kullanicinin ekleyip silebilecegi chart slotlari
(chart_key = slug, title, sort_order). overview_chart_tags artik
bu tabloya FK ile baglanir; chart silinince tag bindingleri cascade siler.

Revision ID: 018
Revises: 017
Create Date: 2026-04-19
"""

from collections.abc import Sequence

from alembic import op

revision: str = "018"
down_revision: str | None = "017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """overview_charts tablosunu olustur ve tag tablosuna FK ekle."""
    op.execute(
        """
        CREATE TABLE overview_charts (
            chart_key TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )
    op.execute(
        """
        ALTER TABLE overview_chart_tags
        ADD CONSTRAINT fk_overview_chart_tags_chart_key
        FOREIGN KEY (chart_key)
        REFERENCES overview_charts(chart_key)
        ON DELETE CASCADE;
        """
    )


def downgrade() -> None:
    """FK'yi kaldir ve overview_charts tablosunu dusur."""
    op.execute(
        """
        ALTER TABLE overview_chart_tags
        DROP CONSTRAINT IF EXISTS fk_overview_chart_tags_chart_key;
        """
    )
    op.execute("DROP TABLE overview_charts;")
