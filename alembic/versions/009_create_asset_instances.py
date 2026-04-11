"""Asset instance tablosunu oluştur.

Her instance, bir asset template'inin somut bir kurulumunu temsil eder
(örn: "Sirkülasyon Pompası #1"). Template silinmek istenirse önce
bağlı instance'lar kaldırılmalıdır (ON DELETE RESTRICT).

Revision ID: 009
Revises: 008
Create Date: 2026-04-11
"""

from collections.abc import Sequence

from alembic import op

revision: str = "009"
down_revision: str | None = "008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """asset_instances tablosunu oluştur."""
    op.execute(
        """
        CREATE TABLE asset_instances (
            id SERIAL PRIMARY KEY,
            template_id INTEGER NOT NULL REFERENCES asset_templates(id) ON DELETE RESTRICT,
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            location TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )


def downgrade() -> None:
    """asset_instances tablosunu kaldır."""
    op.execute("DROP TABLE asset_instances;")
