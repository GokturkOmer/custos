"""service_heartbeats — cross-service watchdog tablosu (V11-105/K13).

v1.1 Paket 02 — 3 katmanlı iç watchdog'un orta katmanı:
  1. systemd ``WatchdogSec=60`` — süreç içi ölü kalma kontrolü (sd_notify)
  2. **service_heartbeats** — DB'ye yazılan heartbeat, cross-service
     kontrol (analytics → critical sağlığı izler)
  3. Dashboard widget (HTMX 30s polling) — operatör görsel onayı

Her servis ana lifespan'ında periyodik olarak ``last_heartbeat_at = NOW()``
yazar. Analytics loop'un cross-check task'ı bu tabloyu okur ve eski
(>180s) servisi alarm üretir (severity=crit, source=watchdog).

Tek satırlı upsert (PRIMARY KEY service_name) — tablo asla büyümez.

Revision ID: 029
Revises: 028
Create Date: 2026-04-27
"""

from collections.abc import Sequence

from alembic import op

revision: str = "029"
down_revision: str | None = "028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """service_heartbeats tablosunu oluştur."""
    op.execute(
        """
        CREATE TABLE service_heartbeats (
            service_name TEXT PRIMARY KEY,
            last_heartbeat_at TIMESTAMPTZ NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            metadata JSONB
        );
        """
    )


def downgrade() -> None:
    """service_heartbeats tablosunu kaldır."""
    op.execute("DROP TABLE service_heartbeats;")
