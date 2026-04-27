"""Resource alarm esik kolonlari (V11-111, P-06).

retention_config singleton tablosuna CPU + RAM uyari esikleri eklenir.
ResourceMonitor (analytics/resource_telemetry.py) bu degerleri her tick'te
okuyup 5 dakikalik pencere ortalamasini esikle karsilastirir.

Default %90 (push pattern: warn severity). UI'da 70-95 araligi range slider.

Revision ID: 033
Revises: 032
Create Date: 2026-04-27
"""

from collections.abc import Sequence

from alembic import op

revision: str = "033"
down_revision: str | None = "032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """retention_config'e CPU + RAM uyari esik kolonlarini ekler."""
    op.execute(
        """
        ALTER TABLE retention_config
            ADD COLUMN IF NOT EXISTS resource_cpu_warn_pct INTEGER
                NOT NULL DEFAULT 90,
            ADD COLUMN IF NOT EXISTS resource_ram_warn_pct INTEGER
                NOT NULL DEFAULT 90;
        """
    )
    # Esik araligi 70-95 (UI slider ile uyumlu); 100'den buyuk veya 50'den
    # kucuk fizyolojik anlamsiz — CHECK ile guvenceye al.
    op.execute(
        """
        ALTER TABLE retention_config
            ADD CONSTRAINT retention_config_cpu_warn_range
                CHECK (resource_cpu_warn_pct BETWEEN 50 AND 99);
        """
    )
    op.execute(
        """
        ALTER TABLE retention_config
            ADD CONSTRAINT retention_config_ram_warn_range
                CHECK (resource_ram_warn_pct BETWEEN 50 AND 99);
        """
    )


def downgrade() -> None:
    """Eklenen kolon ve check'leri kaldirir."""
    op.execute(
        """
        ALTER TABLE retention_config
            DROP CONSTRAINT IF EXISTS retention_config_ram_warn_range,
            DROP CONSTRAINT IF EXISTS retention_config_cpu_warn_range;
        """
    )
    op.execute(
        """
        ALTER TABLE retention_config
            DROP COLUMN IF EXISTS resource_ram_warn_pct,
            DROP COLUMN IF EXISTS resource_cpu_warn_pct;
        """
    )
