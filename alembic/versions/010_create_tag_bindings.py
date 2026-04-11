"""Tag binding tablosunu oluştur.

Bir asset instance'ının template role'lerine hangi tag'lerin
bağlandığını saklar. Her instance-rol çifti tek bir tag'e,
her instance-tag çifti de tek bir role bağlanabilir.

Revision ID: 010
Revises: 009
Create Date: 2026-04-11
"""

from collections.abc import Sequence

from alembic import op

revision: str = "010"
down_revision: str | None = "009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """tag_bindings tablosunu oluştur."""
    op.execute(
        """
        CREATE TABLE tag_bindings (
            id SERIAL PRIMARY KEY,
            instance_id INTEGER NOT NULL REFERENCES asset_instances(id) ON DELETE CASCADE,
            role_id INTEGER NOT NULL REFERENCES template_roles(id) ON DELETE CASCADE,
            tag_id TEXT NOT NULL REFERENCES tags(tag_id) ON DELETE CASCADE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(instance_id, role_id),
            UNIQUE(instance_id, tag_id)
        );
        """
    )


def downgrade() -> None:
    """tag_bindings tablosunu kaldır."""
    op.execute("DROP TABLE tag_bindings;")
