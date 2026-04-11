"""Audit log tablosunu oluştur.

Sistemdeki önemli olayları (alarm tetiklenme, threshold CRUD,
tag/asset işlemleri vb.) kronolojik sırayla saklar.

Revision ID: 013
Revises: 012
Create Date: 2026-04-11
"""

from collections.abc import Sequence

from alembic import op

revision: str = "013"
down_revision: str | None = "012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """audit_log tablosunu oluştur."""
    op.execute(
        """
        CREATE TABLE audit_log (
            id BIGSERIAL PRIMARY KEY,
            timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            category TEXT NOT NULL,
            action TEXT NOT NULL,
            entity_type TEXT NOT NULL DEFAULT '',
            entity_id TEXT NOT NULL DEFAULT '',
            detail TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE INDEX idx_audit_log_time ON audit_log (timestamp DESC);
        CREATE INDEX idx_audit_log_category ON audit_log (category, timestamp DESC);
        """
    )


def downgrade() -> None:
    """audit_log tablosunu kaldır."""
    op.execute("DROP TABLE audit_log;")
