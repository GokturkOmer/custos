"""Push subscription tablosunu oluştur.

Web Push bildirim aboneliklerini saklar. Birden fazla cihaz
(masaüstü + mobil) için endpoint bazlı UNIQUE kısıtı.

Revision ID: 016
Revises: 015
Create Date: 2026-04-12
"""

from collections.abc import Sequence

from alembic import op

revision: str = "016"
down_revision: str | None = "015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """push_subscriptions tablosunu oluştur."""
    op.execute(
        """
        CREATE TABLE push_subscriptions (
            id SERIAL PRIMARY KEY,
            endpoint TEXT NOT NULL UNIQUE,
            p256dh TEXT NOT NULL,
            auth TEXT NOT NULL,
            notify_warn BOOLEAN NOT NULL DEFAULT TRUE,
            notify_crit BOOLEAN NOT NULL DEFAULT TRUE,
            quiet_start TIME DEFAULT NULL,
            quiet_end TIME DEFAULT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )


def downgrade() -> None:
    """push_subscriptions tablosunu kaldır."""
    op.execute("DROP TABLE push_subscriptions;")
