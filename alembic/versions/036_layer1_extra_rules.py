"""Layer 1 ek kurallar — Rate-of-change + Cross-sensor + Severity escalation
(R-06 / V11-304/305/306).

Bu migration uc kanali eklemeyi tek dokunusta tamamlar:

1. **Rate-of-change** (V11-304): ``tags`` tablosuna opsiyonel
   ``rate_of_change_threshold`` (DOUBLE PRECISION). NULL -> kontrol kapali;
   pozitif deger -> ``abs(delta_value / delta_minutes) > esik`` ise alarm.
   Threshold engine her tick'te degerlendirir, cooldown 5 dakika.

2. **Cross-sensor consistency** (V11-305): Iki tag arasi mantiksal kural
   tablosu. ``operator`` six karsilastirma destekler (lt/gt/eq/neq/lte/gte).
   Threshold engine tick sonunda dolu cache uzerinden tarar; cooldown 10 dk.
   ``ON DELETE CASCADE`` — tag silinince kural kalkar.

3. **Severity escalation** (V11-306): ``alarm_events`` tablosuna iki kolon:
   ``escalated_from`` (eski severity, ornek: 'warn') ve ``escalated_at``
   (yukseltilme zamani). Yeni arka plan task ``escalation_loop`` aktif warn
   alarm'lari tarayip ``escalation_warn_to_crit_minutes`` kadar ayakta
   kalanlari otomatik crit'e yukseltir + push tetikler.

   Yukseltme suresi singleton ``retention_config.escalation_warn_to_crit_minutes``
   (INT, default 30, CHECK 5-240) ile globaldir. UI Settings sayfasindan
   developer guncelleyebilir.

Revision ID: 036
Revises: 035
Create Date: 2026-04-28
"""

from collections.abc import Sequence

from alembic import op

revision: str = "036"
down_revision: str | None = "035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Rate-of-change + cross-sensor + escalation kolonlari/tablolari ekler."""
    # 1. Rate-of-change — tags.rate_of_change_threshold
    #    NULL = kontrol devre disi (default davranis, mevcut tag'ler etkilenmez).
    #    Pozitif degeri uygulama katmani dogrular; DB CHECK koymadik cunku UI
    #    boslugu (None) izin verecek.
    op.execute(
        """
        ALTER TABLE tags
            ADD COLUMN rate_of_change_threshold DOUBLE PRECISION;
        """
    )

    # 2. Cross-sensor rules tablosu.
    #    operator CHECK constraint frontend secimleri ile bire bir esler.
    #    severity 4-tier (V11-107) ile uyumlu.
    op.execute(
        """
        CREATE TABLE cross_sensor_rules (
            id BIGSERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            tag_a_id BIGINT NOT NULL
                REFERENCES tags(id) ON DELETE CASCADE,
            tag_b_id BIGINT NOT NULL
                REFERENCES tags(id) ON DELETE CASCADE,
            operator TEXT NOT NULL
                CHECK (operator IN ('lt', 'gt', 'eq', 'neq', 'lte', 'gte')),
            severity TEXT NOT NULL
                CHECK (severity IN ('info', 'warn', 'crit', 'emergency')),
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            description TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CHECK (tag_a_id <> tag_b_id)
        );
        """
    )
    # Tarama indeksi — threshold_engine her tick'te aktif kurallari okur.
    op.execute(
        """
        CREATE INDEX idx_cross_rules_enabled
            ON cross_sensor_rules(enabled)
            WHERE enabled = TRUE;
        """
    )
    # Tag silinmesi sorgu kararli olsun diye iki tag id uzerinden bilesik index.
    op.execute(
        """
        CREATE INDEX idx_cross_rules_tags
            ON cross_sensor_rules(tag_a_id, tag_b_id);
        """
    )

    # 3. Severity escalation — alarm_events'e iki kolon.
    #    escalated_from NULL -> hic yukseltilmedi.
    #    Yukseltme escalation_loop tarafindan update_alarm_event uzerinden
    #    yapilir; insert sirasinda dokunulmaz.
    op.execute(
        """
        ALTER TABLE alarm_events
            ADD COLUMN escalated_from TEXT;
        """
    )
    op.execute(
        """
        ALTER TABLE alarm_events
            ADD COLUMN escalated_at TIMESTAMPTZ;
        """
    )

    # 4. retention_config'e escalation suresi (global, singleton).
    #    Default 30 dakika; range 5-240 (5 dk dust degil, 4 saat tavanI).
    op.execute(
        """
        ALTER TABLE retention_config
            ADD COLUMN escalation_warn_to_crit_minutes INT
                NOT NULL DEFAULT 30
                CHECK (escalation_warn_to_crit_minutes BETWEEN 5 AND 240);
        """
    )

    # 5. alarm_events.source enum'una iki yeni source ekle (rate_of_change +
    #    cross_sensor). Mevcut constraint adi P-05'te konuldu
    #    (``alarm_events_source_enum``); drop + yeniden create ediyoruz.
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
                'threshold',
                'anomaly',
                'liveness',
                'watchdog',
                'rate_of_change',
                'cross_sensor'
            ));
        """
    )


def downgrade() -> None:
    """036 ile eklenen kolonlari/tablolari geri alir.

    Sira: alarm_events source constraint geri eski hale -> ek kolonlar ->
    tablo -> tag kolonu. cross_sensor_rules CASCADE ile FK'lar otomatik
    kalkar; alarm_events kolonlari nullable oldugu icin veri kaybi alarm
    gecmisinde uzun vadeli iz biraktigi 'escalated_*' icin kabul edilebilir
    (downgrade sadece dev/test akisinda kullanilir).

    Ayni gerekce ile source enum'unu eski hale dondurmek de yeni source'ta
    yazilmis kayit varsa fail eder; downgrade once bu kayitlari temizlemeli
    (manuel ya da aciklama: rate_of_change/cross_sensor alarmi yoksa sorun yok).
    """
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
                'threshold',
                'anomaly',
                'liveness',
                'watchdog'
            ));
        """
    )
    op.execute(
        """
        ALTER TABLE retention_config
            DROP COLUMN IF EXISTS escalation_warn_to_crit_minutes;
        """
    )
    op.execute(
        """
        ALTER TABLE alarm_events
            DROP COLUMN IF EXISTS escalated_at;
        """
    )
    op.execute(
        """
        ALTER TABLE alarm_events
            DROP COLUMN IF EXISTS escalated_from;
        """
    )
    op.execute("DROP TABLE IF EXISTS cross_sensor_rules;")
    op.execute(
        """
        ALTER TABLE tags
            DROP COLUMN IF EXISTS rate_of_change_threshold;
        """
    )
