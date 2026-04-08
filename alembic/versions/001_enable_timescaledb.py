"""TimescaleDB extension'ını etkinleştir.

Revision ID: 001
Revises:
Create Date: 2026-04-08
"""

from collections.abc import Sequence

from alembic import op

revision: str = "001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """TimescaleDB extension'ını oluştur."""
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb;")


def downgrade() -> None:
    """TimescaleDB extension'ını kaldır."""
    op.execute("DROP EXTENSION IF EXISTS timescaledb;")
