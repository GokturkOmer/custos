"""Tag tanım tablosunu oluştur.

Her tag bir Modbus register okuma noktasını temsil eder.
tag_readings.tag_id ile mantıksal ilişki var ama FK constraint
eklenmez — hypertable'larda FK performans sorununa yol açar.

Revision ID: 006
Revises: 005
Create Date: 2026-04-11
"""

from collections.abc import Sequence

from alembic import op

revision: str = "006"
down_revision: str | None = "005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """tags tablosunu oluştur."""
    op.execute(
        """
        CREATE TABLE tags (
            id SERIAL PRIMARY KEY,
            tag_id TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            modbus_host TEXT NOT NULL,
            modbus_port INTEGER NOT NULL DEFAULT 502,
            unit_id INTEGER NOT NULL DEFAULT 1,
            register_address INTEGER NOT NULL,
            register_type TEXT NOT NULL DEFAULT 'uint16',
            byte_order TEXT NOT NULL DEFAULT 'big',
            gain DOUBLE PRECISION NOT NULL DEFAULT 1.0,
            "offset" DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            unit TEXT NOT NULL DEFAULT '',
            polling_interval_ms INTEGER NOT NULL DEFAULT 10000,
            polling_preset TEXT NOT NULL DEFAULT 'slow',
            status TEXT NOT NULL DEFAULT 'active',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )


def downgrade() -> None:
    """tags tablosunu kaldır."""
    op.execute("DROP TABLE tags;")
