"""overview_charts tablosuna time_window_minutes kolonu ekle.

Her chart kendi zaman aralığını saklar (default 30 dakika).
Kullanıcı detay sayfasında 15m / 30m / 1h / 6h / 24h seçer.

Revision ID: 019
Revises: 018
Create Date: 2026-04-19
"""

from collections.abc import Sequence

from alembic import op

revision: str = "019"
down_revision: str | None = "018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """time_window_minutes kolonunu ekle (default 30)."""
    op.execute(
        """
        ALTER TABLE overview_charts
        ADD COLUMN time_window_minutes INTEGER NOT NULL DEFAULT 30;
        """
    )


def downgrade() -> None:
    """time_window_minutes kolonunu kaldir."""
    op.execute("ALTER TABLE overview_charts DROP COLUMN time_window_minutes;")
