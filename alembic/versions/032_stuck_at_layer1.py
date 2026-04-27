"""Stuck-at Layer 1 — sensör donma tespiti (V11-108, P-05).

v1.1 Paket 05 — Hibrit yaklaşımın kural katmanı:

- ``tags.stuck_at_preset`` (NOT NULL DEFAULT 'auto'): Tag tipine göre
  hardcoded eşik kategorisi. ``auto`` ise ``shared.stuck_at_presets``
  modülü ``unit`` alanından preset'i türetir. ``none`` kontrol kapatır.
- ``tags.stuck_at_seconds`` (NULLABLE): Manuel saniye override; doluysa
  preset'in yerine geçer.

P-04'te ``alarm_events`` tablosuna ``is_test`` eklenmişti — threshold
kaynaklı alarm'ları bakım modunda işaretlemek için. P-05 alarm
kaynaklarını çoğullaştırıyor (threshold + anomaly + liveness + watchdog),
bu yüzden tabloya 3 kolon daha ekliyoruz:

- ``alarm_events.source`` (NOT NULL DEFAULT 'threshold' + CHECK enum):
  Alarmı üreten alt sistem. Geri uyumluluk için default 'threshold' —
  mevcut satırlar otomatik bu değeri alır.
- ``alarm_events.severity`` (NOT NULL DEFAULT 'warn' + CHECK 4-tier):
  Threshold'sız alarmlar (liveness/watchdog) için denormalize severity.
  Threshold kaynaklı alarmlarda ``threshold_engine`` insert sırasında
  threshold.severity'yi explicit set eder.
- ``alarm_events.message`` (NOT NULL DEFAULT ''): Liveness ve diğer
  kural-bazlı kaynakların kullanıcıya gösterilecek açıklaması (ör.
  "Sensör donuk: 1820s'dir değer değişmedi").

``alarm_events.threshold_id`` NULLABLE'a düşer — liveness ve watchdog
alarmlarının threshold'u yoktur. Mevcut satırlar etkilenmez (zaten dolu).

Index ``(source, triggered_at DESC)`` alarm sayfası "Tip" filtresi için.

Revision ID: 032
Revises: 031
Create Date: 2026-04-27
"""

from collections.abc import Sequence

from alembic import op

revision: str = "032"
down_revision: str | None = "031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Stuck-at preset kolonlarını + alarm_events çoklu kaynak şemasını ekler."""
    # tags: stuck-at preset + override
    op.execute(
        """
        ALTER TABLE tags
            ADD COLUMN IF NOT EXISTS stuck_at_preset TEXT NOT NULL
                DEFAULT 'auto',
            ADD COLUMN IF NOT EXISTS stuck_at_seconds INTEGER;
        """
    )
    op.execute(
        """
        ALTER TABLE tags
            DROP CONSTRAINT IF EXISTS stuck_at_preset_enum;
        """
    )
    op.execute(
        """
        ALTER TABLE tags
            ADD CONSTRAINT stuck_at_preset_enum
            CHECK (stuck_at_preset IN (
                'auto', 'none', 'fast', 'slow', 'very_slow', 'counter'
            ));
        """
    )

    # alarm_events: threshold_id NULLABLE (liveness + watchdog için)
    op.execute(
        """
        ALTER TABLE alarm_events
            ALTER COLUMN threshold_id DROP NOT NULL;
        """
    )

    # alarm_events: source + severity + message kolonları
    op.execute(
        """
        ALTER TABLE alarm_events
            ADD COLUMN IF NOT EXISTS source TEXT NOT NULL
                DEFAULT 'threshold',
            ADD COLUMN IF NOT EXISTS severity TEXT NOT NULL
                DEFAULT 'warn',
            ADD COLUMN IF NOT EXISTS message TEXT NOT NULL DEFAULT '';
        """
    )
    op.execute(
        """
        ALTER TABLE alarm_events
            DROP CONSTRAINT IF EXISTS alarm_events_source_enum;
        """
    )
    op.execute(
        """
        ALTER TABLE alarm_events
            ADD CONSTRAINT alarm_events_source_enum
            CHECK (source IN (
                'threshold', 'anomaly', 'liveness', 'watchdog'
            ));
        """
    )
    op.execute(
        """
        ALTER TABLE alarm_events
            DROP CONSTRAINT IF EXISTS alarm_events_severity_enum;
        """
    )
    op.execute(
        """
        ALTER TABLE alarm_events
            ADD CONSTRAINT alarm_events_severity_enum
            CHECK (severity IN ('info', 'warn', 'crit', 'emergency'));
        """
    )

    # Alarm sayfası "Tip" filtresi için indeks
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_alarm_events_source
            ON alarm_events (source, triggered_at DESC);
        """
    )


def downgrade() -> None:
    """Stuck-at + alarm_events çoklu kaynak kolonlarını kaldırır.

    DİKKAT: Liveness/watchdog kaynaklı alarm satırları varsa
    ``threshold_id`` NOT NULL'a geri dönerken hata verir — önce manuel
    silmek gerekir.
    """
    op.execute("DROP INDEX IF EXISTS idx_alarm_events_source;")
    op.execute(
        """
        ALTER TABLE alarm_events
            DROP CONSTRAINT IF EXISTS alarm_events_severity_enum;
        """
    )
    op.execute(
        """
        ALTER TABLE alarm_events
            DROP CONSTRAINT IF EXISTS alarm_events_source_enum;
        """
    )
    op.execute(
        """
        ALTER TABLE alarm_events
            DROP COLUMN IF EXISTS message,
            DROP COLUMN IF EXISTS severity,
            DROP COLUMN IF EXISTS source;
        """
    )
    op.execute(
        """
        ALTER TABLE alarm_events
            ALTER COLUMN threshold_id SET NOT NULL;
        """
    )
    op.execute(
        """
        ALTER TABLE tags
            DROP CONSTRAINT IF EXISTS stuck_at_preset_enum;
        """
    )
    op.execute(
        """
        ALTER TABLE tags
            DROP COLUMN IF EXISTS stuck_at_seconds,
            DROP COLUMN IF EXISTS stuck_at_preset;
        """
    )
