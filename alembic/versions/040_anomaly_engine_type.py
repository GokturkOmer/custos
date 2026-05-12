"""anomaly_scores tablosuna engine_type kolonu (Faz 1.3 wind pivot).

Wind pivot Faz 1.3 (2026-05-12). Mevcut Isolation Forest skoru ile yeni
MLPRegressor autoencoder skoru ayni tabloda saklanir; engine_type kolonu
bu iki kaynagi ayirir.

Tasarim:
- Tum DB'lere (custos, custos_wind, custos_endurance) uniform uygulanir;
  ``current_database`` guard YOK (kolon eklemek istemsiz veri yazimi
  degil, sema standartlasmasi).
- Default 'if' — mevcut satirlar otomatik bu degeri alir (geri uyumlu).
- ``IF NOT EXISTS`` ile idempotent (yeniden kosturulurda fail etmez).
- Index eklenmez — sorgular zaten ``(instance_id, timestamp DESC)``
  uzerinden cikiyor; engine_type filtresi ek selectivity kazandirmaz
  (default'ta 1-2 deger).

Engine_type degerleri:
- ``'if'``: Isolation Forest (default, geri uyumlu) — AVM ve wind ortak.
- ``'ae'``: Autoencoder (MLPRegressor) — sadece wind pivot.

Revision ID: 040
Revises: 039
Create Date: 2026-05-12
"""

from collections.abc import Sequence

from alembic import op

revision: str = "040"
down_revision: str | None = "039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """``engine_type VARCHAR(20) NOT NULL DEFAULT 'if'`` kolonunu ekler.

    PostgreSQL 9.6+ ``ADD COLUMN IF NOT EXISTS`` ile idempotent. Mevcut
    satirlar default deger ('if') ile geri-doldurulur — IF tabanli
    eski skorlar etiketlenmemis kalmaz.
    """
    op.execute(
        "ALTER TABLE anomaly_scores "
        "ADD COLUMN IF NOT EXISTS engine_type VARCHAR(20) NOT NULL DEFAULT 'if'",
    )


def downgrade() -> None:
    """``engine_type`` kolonunu kaldirir (autoencoder skorlari kaybolur)."""
    op.execute(
        "ALTER TABLE anomaly_scores DROP COLUMN IF EXISTS engine_type",
    )
