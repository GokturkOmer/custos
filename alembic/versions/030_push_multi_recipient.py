"""Push çoklu alıcı kolonları + global master switch (V11-103, K3, K10).

v1.1 Paket 03 — Şu an ``push_subscriptions`` cihaz başına anonim ve sadece
``notify_warn`` / ``notify_crit`` ayrımı var. Bu migration ile çoklu kişi
senaryosu desteklenir:

- ``label``                 : İnsana okunabilir etiket (örn. "Ali — Telefon").
- ``enabled``               : Tek-tıkla bildirim sustur (cihazı silmeden).
- ``notify_info``           : 4-tier severity'nin ``info`` katmanı (P-02 sonrası
  ``info`` için ``notify_warn`` fallback yapılıyordu — bu kolon ile ayrıştı).
- ``notify_emergency``      : ``emergency`` katmanı default TRUE (insan hayatı /
  operasyon riski — sessiz saat zaten bypass).
- ``created_by_user_id``    : Operator kendi aboneliğini düzenleyebilir;
  başkasınınkine yetkisiz erişim app-katmanında 403 ile engellenir
  (K3 — basitlik için DB check değil app-side enforcement).

``retention_config.push_global_enabled`` master switch — tatil/eğitim sırasında
tüm bildirimleri tek anahtarla durdurmak için (K3 + S3). Default TRUE,
mevcut davranışı bozmaz.

Geri alma davranışı: kolonlar drop edilir; veri kaybı uyarısı v1_1_plan.md'de.

Revision ID: 030
Revises: 029
Create Date: 2026-04-27
"""

from collections.abc import Sequence

from alembic import op

revision: str = "030"
down_revision: str | None = "029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Push çoklu alıcı kolonlarını + master switch'i ekler."""
    # push_subscriptions: 5 yeni kolon. created_by_user_id ON DELETE SET NULL
    # — kullanıcı silinince abonelik anonimleşir, push'lar kesilmesin.
    op.execute(
        """
        ALTER TABLE push_subscriptions
            ADD COLUMN IF NOT EXISTS label TEXT NOT NULL DEFAULT '',
            ADD COLUMN IF NOT EXISTS enabled BOOLEAN NOT NULL DEFAULT TRUE,
            ADD COLUMN IF NOT EXISTS notify_info BOOLEAN NOT NULL DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS notify_emergency BOOLEAN NOT NULL DEFAULT TRUE,
            ADD COLUMN IF NOT EXISTS created_by_user_id INTEGER
                REFERENCES users(id) ON DELETE SET NULL;
        """
    )

    # retention_config: master switch. Singleton tablo (id=1), satır migration
    # 026'dan beri var — kolon eklemek yeterli.
    op.execute(
        """
        ALTER TABLE retention_config
            ADD COLUMN IF NOT EXISTS push_global_enabled
                BOOLEAN NOT NULL DEFAULT TRUE;
        """
    )


def downgrade() -> None:
    """Eklenen kolonları kaldırır (etiket ve filtre tercihleri kaybolur)."""
    op.execute(
        """
        ALTER TABLE push_subscriptions
            DROP COLUMN IF EXISTS created_by_user_id,
            DROP COLUMN IF EXISTS notify_emergency,
            DROP COLUMN IF EXISTS notify_info,
            DROP COLUMN IF EXISTS enabled,
            DROP COLUMN IF EXISTS label;
        """
    )
    op.execute(
        """
        ALTER TABLE retention_config
            DROP COLUMN IF EXISTS push_global_enabled;
        """
    )
