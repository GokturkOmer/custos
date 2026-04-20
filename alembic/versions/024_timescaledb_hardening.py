"""TimescaleDB production hardening — chunk interval, compression, retention.

F11 Paket A: tag_readings + features hypertable'larına production-grade
ayarlar uygulanır.

- Chunk interval: 1 gün (sorgu performansı + compression chunk sınırı)
- Compression policy: 7 gün sonra otomatik (segmentby tag_id, orderby ts DESC)
- Retention policy: 365 gün (altyapı vizyon özeti §2.1)

`compress_segmentby='tag_id'` şarttır — tek-tag sorgularında decompress
overhead'i minimize eder. Yanlış kurulursa performans ters döner.

Not: Chunk interval downgrade'de geri alınamaz (TimescaleDB sınırlaması).
Diğer ayarlar (compression, policies) downgrade'de temizlenir.

Revision ID: 024
Revises: 023
Create Date: 2026-04-20
"""

from collections.abc import Sequence

from alembic import op

revision: str = "024"
down_revision: str | None = "023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Hypertable'lara chunk/compression/retention ayarlarını uygula."""
    # tag_readings — ham sensör okumaları (saniyelik)
    op.execute(
        "SELECT set_chunk_time_interval('tag_readings', INTERVAL '1 day');"
    )
    op.execute(
        """
        ALTER TABLE tag_readings SET (
            timescaledb.compress,
            timescaledb.compress_segmentby = 'tag_id',
            timescaledb.compress_orderby = 'timestamp DESC'
        );
        """
    )
    op.execute(
        "SELECT add_compression_policy('tag_readings', INTERVAL '7 days');"
    )
    op.execute(
        "SELECT add_retention_policy('tag_readings', INTERVAL '365 days');"
    )

    # features — hesaplanmış özellikler (ham veri türevi)
    op.execute(
        "SELECT set_chunk_time_interval('features', INTERVAL '1 day');"
    )
    op.execute(
        """
        ALTER TABLE features SET (
            timescaledb.compress,
            timescaledb.compress_segmentby = 'tag_id',
            timescaledb.compress_orderby = 'timestamp DESC'
        );
        """
    )
    op.execute(
        "SELECT add_compression_policy('features', INTERVAL '7 days');"
    )
    op.execute(
        "SELECT add_retention_policy('features', INTERVAL '365 days');"
    )


def downgrade() -> None:
    """Policy'leri kaldır, compression'ı kapat.

    Chunk interval geri alınamaz — TimescaleDB sınırlaması. Yeni chunk'lar
    eski default (7 gün) ile oluşturulmaz; mevcut hypertable'ın chunk
    interval'ı son set edilen değerde kalır.

    Compression kapatılırken mevcut sıkıştırılmış chunk'lar önce decompress
    edilir; aksi halde ALTER hata verir.
    """
    # features — önce policy'ler, sonra decompress, sonra compression off
    op.execute(
        "SELECT remove_retention_policy('features', if_exists => true);"
    )
    op.execute(
        "SELECT remove_compression_policy('features', if_exists => true);"
    )
    op.execute(
        """
        DO $$
        DECLARE
            chunk_record record;
        BEGIN
            FOR chunk_record IN
                SELECT format('%I.%I', chunk_schema, chunk_name) AS qname
                FROM timescaledb_information.chunks
                WHERE hypertable_name = 'features' AND is_compressed = true
            LOOP
                EXECUTE format('SELECT decompress_chunk(%L::regclass)',
                    chunk_record.qname);
            END LOOP;
        END $$;
        """
    )
    op.execute("ALTER TABLE features SET (timescaledb.compress = false);")

    # tag_readings — aynı sıralama
    op.execute(
        "SELECT remove_retention_policy('tag_readings', if_exists => true);"
    )
    op.execute(
        "SELECT remove_compression_policy('tag_readings', if_exists => true);"
    )
    op.execute(
        """
        DO $$
        DECLARE
            chunk_record record;
        BEGIN
            FOR chunk_record IN
                SELECT format('%I.%I', chunk_schema, chunk_name) AS qname
                FROM timescaledb_information.chunks
                WHERE hypertable_name = 'tag_readings' AND is_compressed = true
            LOOP
                EXECUTE format('SELECT decompress_chunk(%L::regclass)',
                    chunk_record.qname);
            END LOOP;
        END $$;
        """
    )
    op.execute("ALTER TABLE tag_readings SET (timescaledb.compress = false);")
