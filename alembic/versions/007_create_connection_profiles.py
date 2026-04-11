"""Connection profile tablosunu oluştur.

Her profil bir Modbus TCP bağlantı noktasını temsil eder.
Auto-scan bu profiller üzerinden çalışır.

Revision ID: 007
Revises: 006
Create Date: 2026-04-11
"""

from collections.abc import Sequence

from alembic import op

revision: str = "007"
down_revision: str | None = "006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """connection_profiles tablosunu oluştur."""
    op.execute(
        """
        CREATE TABLE connection_profiles (
            id SERIAL PRIMARY KEY,
            name TEXT UNIQUE NOT NULL,
            host TEXT NOT NULL,
            port INTEGER NOT NULL DEFAULT 502,
            unit_id_start INTEGER NOT NULL DEFAULT 1,
            unit_id_end INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'idle',
            last_scan_at TIMESTAMPTZ,
            slave_latency_min_ms DOUBLE PRECISION,
            slave_latency_avg_ms DOUBLE PRECISION,
            slave_latency_max_ms DOUBLE PRECISION,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )


def downgrade() -> None:
    """connection_profiles tablosunu kaldır."""
    op.execute("DROP TABLE connection_profiles;")
