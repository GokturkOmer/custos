"""retention_config singleton tablosu — runtime retention kontrolü (F11 Paket F).

Kullanıcı Settings → Veri Saklama ekranından ham ``tag_readings`` retention
süresini (30/60/180/365 gün) değiştirebilir veya otomatik temizlemeyi
("auto-clean off") kapatabilir. Tek satır yeterli — sistem-geneli ayar;
``CHECK (id = 1)`` singleton pattern'i ile garantilenir.

TimescaleDB retention policy'sinin kendisi F11 Paket A migration 024'te
kurulmuştu (``add_retention_policy('tag_readings', '365 days')``). Bu tablo
sadece kullanıcının tercihini kalıcı tutar; policy senkronizasyonu runtime'da
``DatabaseInterface.update_retention_config`` içinden yapılır.

Revision ID: 026
Revises: 025
Create Date: 2026-04-21
"""

from collections.abc import Sequence

from alembic import op

revision: str = "026"
down_revision: str | None = "025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """retention_config tablosunu oluştur ve varsayılan satırı yaz."""
    op.execute(
        """
        CREATE TABLE retention_config (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            raw_retention_days INTEGER NOT NULL DEFAULT 365
                CHECK (raw_retention_days > 0),
            auto_clean_enabled BOOLEAN NOT NULL DEFAULT TRUE,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_by TEXT NOT NULL DEFAULT 'system'
        );
        """
    )
    op.execute(
        "INSERT INTO retention_config (id, raw_retention_days, "
        "auto_clean_enabled, updated_by) "
        "VALUES (1, 365, TRUE, 'system');"
    )


def downgrade() -> None:
    """Tabloyu kaldır. Policy kendisi migration 024'e bağlı — dokunulmaz."""
    op.execute("DROP TABLE IF EXISTS retention_config;")
