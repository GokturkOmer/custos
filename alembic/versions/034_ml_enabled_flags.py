"""ML hub icin enable/disable flag'leri (R-04).

Iki yeni kolon:

- ``asset_instances.ml_enabled BOOLEAN NOT NULL DEFAULT TRUE``: per-instance
  ML inference acik/kapali switch'i. AnomalyDetector tick'te bu flag'i okur,
  False olan instance'lari atlar.
- ``retention_config.ml_inference_enabled BOOLEAN NOT NULL DEFAULT TRUE``:
  sistem-geneli ML inference master switch (push_global_enabled benzeri).
  False iken hicbir instance icin inference yapilmaz; per-instance flag
  irrelevant olur.

Default TRUE — geriye uyumlu davranis (mevcut anomaly detection deneyimi
degismez). UI tarafinda ML hub developer-only acilir, kullanici manuel
olarak kapatabilir.

Revision ID: 034
Revises: 033
Create Date: 2026-04-28
"""

from collections.abc import Sequence

from alembic import op

revision: str = "034"
down_revision: str | None = "033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """ML hub icin per-instance ve global enable flag'lerini ekler."""
    # Per-instance ml_enabled — AnomalyDetector tick'te respect eder.
    op.execute(
        """
        ALTER TABLE asset_instances
            ADD COLUMN IF NOT EXISTS ml_enabled BOOLEAN NOT NULL DEFAULT TRUE;
        """
    )
    # Global master switch — retention_config singleton satirinda. Push
    # master switch (push_global_enabled) ile ayni desen.
    op.execute(
        """
        ALTER TABLE retention_config
            ADD COLUMN IF NOT EXISTS ml_inference_enabled BOOLEAN NOT NULL DEFAULT TRUE;
        """
    )


def downgrade() -> None:
    """Eklenen kolonlari kaldirir."""
    op.execute(
        """
        ALTER TABLE retention_config
            DROP COLUMN IF EXISTS ml_inference_enabled;
        """
    )
    op.execute(
        """
        ALTER TABLE asset_instances
            DROP COLUMN IF EXISTS ml_enabled;
        """
    )
