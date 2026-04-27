"""Severity 4-tier — info/warn/crit/emergency CHECK constraint (V11-107, K10).

v1.1 Paket 02 — ISA-18.2 standardına uygun 4 katman alarm severity'si.
Mevcut ``thresholds.severity`` TEXT default 'warn' alanına CHECK constraint
eklenir. Mevcut kayıtlar (warn/crit) uyumlu; info/emergency için alan açılır.

Emergency davranışı (runtime):
- ``threshold_engine._can_clear_with_hysteresis`` emergency'de False döner
  (manuel acknowledge zorunlu — yanlışlıkla auto-clear olmasın).
- ``threshold_engine._handle_breach_no_alarm`` emergency'de debounce'u 1 sn'ye
  override eder (kritik gecikme yok).
- ``push_sender._should_notify`` emergency'de quiet-hour bypass.

Push subscription tablosuna ``notify_info`` / ``notify_emergency`` kolonları
eklenmedi — P-03 (push çoklu alıcı) paketinde gelecek. Bu pakette
``_should_notify`` info → notify_warn fallback, emergency → her zaman
gönderir.

Revision ID: 028
Revises: 027
Create Date: 2026-04-27
"""

from collections.abc import Sequence

from alembic import op

revision: str = "028"
down_revision: str | None = "027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """severity_enum CHECK constraint'i ekler — 4 katman."""
    # IF EXISTS — eski upgrade/downgrade çalıştırıldıysa idempotent kalsın.
    op.execute(
        """
        ALTER TABLE thresholds
            DROP CONSTRAINT IF EXISTS severity_enum;
        """
    )
    op.execute(
        """
        ALTER TABLE thresholds
            ADD CONSTRAINT severity_enum
            CHECK (severity IN ('info', 'warn', 'crit', 'emergency'));
        """
    )


def downgrade() -> None:
    """severity_enum constraint'i kaldırır."""
    op.execute(
        """
        ALTER TABLE thresholds
            DROP CONSTRAINT IF EXISTS severity_enum;
        """
    )
