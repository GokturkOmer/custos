"""Mode-aware + SPC iskelet (R-07 / V11-307/308).

Iki Layer 2 ozellik tek migration'da:

1. **Mode-aware** (V11-307): ``asset_instances`` tablosuna 3 yeni kolon:
   - ``operating_mode TEXT NOT NULL DEFAULT 'running'`` —
     ``running`` (anomali aktif, default), ``startup`` (anomali sustu),
     ``shutdown`` (anomali sustu), ``idle`` (anomali aktif). AnomalyDetector
     tick'te startup/shutdown modlarinda alarm yazimini atlar; running ve
     idle'da normal calisir. Operator manuel toggle eder; otomatik gecis
     yok (Faz 3 V11-303 ile gelecek).
   - ``operating_mode_changed_at TIMESTAMPTZ`` — UI'da "X dakikadir startup'ta"
     gostergesi icin. Manuel toggle'da guncellenir.
   - ``operating_mode_changed_by_user_id INTEGER REFERENCES users(id)
     ON DELETE SET NULL`` — audit izi.

2. **SPC iskelet** (V11-308): tag basina EWMA + CUSUM + MAD streaming
   istatistikleri. Yeni motor ``analytics/spc_engine.py`` 5 dk tick.
   Ilk 100 ornek ogrenme penceresi (sessiz); sonrasinda sapma alarmi yazar
   (``source='spc'``, severity='warn').

   - ``tags.spc_enabled BOOLEAN NOT NULL DEFAULT FALSE`` — per-tag opt-in.
     Default kapali; pilot operatoru ihtiyaca gore acar.
   - ``spc_state`` tablosu — tag bazinda streaming state (EWMA mean +
     variance, CUSUM pos/neg, MAD median + value, sample count, learning
     complete). Server restart icin diske yazilir.
   - ``alarm_events.source`` enum'una ``'spc'`` eklenir (R-06 deseni:
     DROP + ADD CONSTRAINT).

Revision ID: 037
Revises: 036
Create Date: 2026-04-28
"""

from collections.abc import Sequence

from alembic import op

revision: str = "037"
down_revision: str | None = "036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Mode-aware + SPC kolonlari/tablolari ekler."""
    # 1. Mode-aware (V11-307) — asset_instances'e 3 kolon.
    #    operating_mode CHECK enum'u UI dropdown ile bire bir esler.
    #    Default 'running' (mevcut instance'lar etkilenmez, anomali aktif kalir).
    op.execute(
        """
        ALTER TABLE asset_instances
            ADD COLUMN operating_mode TEXT NOT NULL DEFAULT 'running'
                CHECK (operating_mode IN (
                    'running', 'startup', 'shutdown', 'idle'
                ));
        """
    )
    op.execute(
        """
        ALTER TABLE asset_instances
            ADD COLUMN operating_mode_changed_at TIMESTAMPTZ;
        """
    )
    op.execute(
        """
        ALTER TABLE asset_instances
            ADD COLUMN operating_mode_changed_by_user_id INTEGER
                REFERENCES users(id) ON DELETE SET NULL;
        """
    )

    # 2. SPC iskelet (V11-308) — tags.spc_enabled per-tag opt-in.
    #    Default FALSE: mevcut tag'ler etkilenmez; pilot operator
    #    sensor_form'dan acar.
    op.execute(
        """
        ALTER TABLE tags
            ADD COLUMN spc_enabled BOOLEAN NOT NULL DEFAULT FALSE;
        """
    )

    # 3. spc_state tablosu — tag bazinda streaming istatistikler.
    #    UNIQUE(tag_id) tek satir per-tag garantisi (engine upsert).
    #    ON DELETE CASCADE: tag silininca state otomatik dusurulur.
    #    EWMA/CUSUM/MAD alanlari NULL ile baslar (henuz ornek alinmadi).
    op.execute(
        """
        CREATE TABLE spc_state (
            id BIGSERIAL PRIMARY KEY,
            tag_id TEXT NOT NULL UNIQUE
                REFERENCES tags(tag_id) ON DELETE CASCADE,
            sample_count INTEGER NOT NULL DEFAULT 0,
            ewma_value DOUBLE PRECISION,
            ewma_variance DOUBLE PRECISION,
            cusum_pos DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            cusum_neg DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            mad_median DOUBLE PRECISION,
            mad_value DOUBLE PRECISION,
            last_sample_at TIMESTAMPTZ,
            learning_complete BOOLEAN NOT NULL DEFAULT FALSE,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )
    # Engine warm-up icin tag_id lookup; UNIQUE'in yarattigi index zaten
    # yeterli ama acik isimle bagimsiz tutuyoruz (geriye donuk yumusakca).
    op.execute(
        """
        CREATE INDEX idx_spc_state_tag ON spc_state(tag_id);
        """
    )

    # 4. alarm_events.source enum'una 'spc' ekle (R-06 deseni; Migration 036
    #    ile ayni ad: ``alarm_events_source_enum``). Drop + recreate; mevcut
    #    'spc' source'lu kayit yok (yeni eklenen) — fail riski yok.
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
                'cross_sensor',
                'spc'
            ));
        """
    )


def downgrade() -> None:
    """037 ile eklenen kolonlari/tablolari geri alir.

    Sira: alarm_events source constraint geri eski hale -> spc_state tablosu ->
    tags.spc_enabled -> asset_instances mode kolonlari.

    Source enum'unu eski hale dondurmek 'spc' source'lu kayit varsa fail
    eder; downgrade once bu kayitlari temizlemeli (manuel ya da pilot
    sirasinda hic SPC alarmi yazilmadiysa sorun yok). Downgrade dev/test
    akisinda kullanildigi icin uretim verisi varsa zaten manuel temizlik
    gerekir.
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
                'watchdog',
                'rate_of_change',
                'cross_sensor'
            ));
        """
    )
    op.execute("DROP TABLE IF EXISTS spc_state;")
    op.execute(
        """
        ALTER TABLE tags
            DROP COLUMN IF EXISTS spc_enabled;
        """
    )
    op.execute(
        """
        ALTER TABLE asset_instances
            DROP COLUMN IF EXISTS operating_mode_changed_by_user_id;
        """
    )
    op.execute(
        """
        ALTER TABLE asset_instances
            DROP COLUMN IF EXISTS operating_mode_changed_at;
        """
    )
    op.execute(
        """
        ALTER TABLE asset_instances
            DROP COLUMN IF EXISTS operating_mode;
        """
    )
