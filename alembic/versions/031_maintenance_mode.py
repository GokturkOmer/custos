"""Bakım modu kolonları (V11-104, P-04).

v1.1 Paket 04 — Per-instance + global bakım modu için kalıcılık katmanı.

- ``asset_instances`` üzerinde 4 kolon: bakım bitiş zamanı, sebep, başlatan
  kullanıcı ve başlangıç zamanı. Üçü birden NULL → bakımda değil; ``until``
  geçmiş bir zaman → süresi dolmuş, ``expire_check_loop`` otomatik kapatır.
- ``alarm_events.is_test``: bakım sırasında üretilen alarm'lar bu flag ile
  yazılır → push gönderilmez, anomali eğitiminde filtre (P-12'de tam) ve
  alarms sayfasında görsel ayrım yapılır. Indeks alarms listesi için
  ``(is_test, triggered_at DESC)``.
- ``retention_config`` üzerinde global bakım kolonları: aynı semantik
  (until/reason/started_by/started_at). Singleton tablo (id=1) olduğu için
  sistem-geneli ayar burada tutuluyor (push master switch ile aynı pattern).

ON DELETE SET NULL — kullanıcı silindiğinde bakım kaydı kaybolmasın, audit
izlenebilirliği korunmuş olsun (P-03 push_subscriptions ile aynı yaklaşım).

Revision ID: 031
Revises: 030
Create Date: 2026-04-27
"""

from collections.abc import Sequence

from alembic import op

revision: str = "031"
down_revision: str | None = "030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Bakım modu kolonlarını + alarm is_test flag'ini ekler."""
    # asset_instances: per-instance bakım modu kolonları
    op.execute(
        """
        ALTER TABLE asset_instances
            ADD COLUMN IF NOT EXISTS maintenance_mode_until TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS maintenance_reason TEXT NOT NULL DEFAULT '',
            ADD COLUMN IF NOT EXISTS maintenance_started_by_user_id INTEGER
                REFERENCES users(id) ON DELETE SET NULL,
            ADD COLUMN IF NOT EXISTS maintenance_started_at TIMESTAMPTZ;
        """
    )

    # alarm_events.is_test: bakım modunda üretilen alarm'ları işaretler
    op.execute(
        """
        ALTER TABLE alarm_events
            ADD COLUMN IF NOT EXISTS is_test BOOLEAN NOT NULL DEFAULT FALSE;
        """
    )
    # Alarms listesinde "is_test gizle/göster" filtresi için indeks
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_alarm_events_is_test
            ON alarm_events (is_test, triggered_at DESC);
        """
    )

    # retention_config: global bakım modu kolonları (singleton id=1)
    op.execute(
        """
        ALTER TABLE retention_config
            ADD COLUMN IF NOT EXISTS global_maintenance_until TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS global_maintenance_reason TEXT
                NOT NULL DEFAULT '',
            ADD COLUMN IF NOT EXISTS global_maintenance_started_by_user_id
                INTEGER REFERENCES users(id) ON DELETE SET NULL,
            ADD COLUMN IF NOT EXISTS global_maintenance_started_at TIMESTAMPTZ;
        """
    )


def downgrade() -> None:
    """Eklenen kolonları kaldırır (aktif bakım kayıtları kaybolur)."""
    op.execute(
        """
        ALTER TABLE retention_config
            DROP COLUMN IF EXISTS global_maintenance_started_at,
            DROP COLUMN IF EXISTS global_maintenance_started_by_user_id,
            DROP COLUMN IF EXISTS global_maintenance_reason,
            DROP COLUMN IF EXISTS global_maintenance_until;
        """
    )
    op.execute("DROP INDEX IF EXISTS idx_alarm_events_is_test;")
    op.execute(
        """
        ALTER TABLE alarm_events
            DROP COLUMN IF EXISTS is_test;
        """
    )
    op.execute(
        """
        ALTER TABLE asset_instances
            DROP COLUMN IF EXISTS maintenance_started_at,
            DROP COLUMN IF EXISTS maintenance_started_by_user_id,
            DROP COLUMN IF EXISTS maintenance_reason,
            DROP COLUMN IF EXISTS maintenance_mode_until;
        """
    )
