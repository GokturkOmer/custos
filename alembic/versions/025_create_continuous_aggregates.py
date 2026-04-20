"""Continuous aggregates — 1 dakika + 1 saat (hierarchical).

F11 Paket B: TimescaleDB continuous aggregate'leri kurar. İki katman:

- ``tag_readings_1min``: ham veriden türer, AVG/MIN/MAX/STDDEV/COUNT per tag
  per dakika. 5 dakikada bir refresh. 3 yıl retention.
- ``tag_readings_1hour``: **1min agregatından türer** (hierarchical CA,
  daha hızlı refresh). 30 dakikada bir refresh. Retention yok (sınırsız).

STDDEV yaklaşımı (hierarchical'da önemli not): 1 saatlik ``stddev_value``,
60 dakikalık STDDEV'lerin ağırlıksız ortalamasıdır — matematiksel olarak
exact pooled-variance değil, yaklaşıktır. Kabul edilebilir çünkü (a) saat
agregatı uzun-vadeli trend içindir, STDDEV birincil metrik değildir;
(b) exact değer istenirse ham veriden veya 1min agregatından hesaplanır.

Backfill not dahil edilmedi: ``CALL refresh_continuous_aggregate()`` açık
transaction içinde çalışamaz, alembic ise migration'ı tek transaction'da
çalıştırır. Mevcut veriyi backfill etmek için
``scripts/refresh_continuous_aggregates.py`` elle bir kez çalıştırılır
(boş DB'de no-op; migration sonrası refresh policy 5-30 dakika içinde
yeni veriyi zaten materialize eder).

Revision ID: 025
Revises: 024
Create Date: 2026-04-20
"""

from collections.abc import Sequence

from alembic import op

revision: str = "025"
down_revision: str | None = "024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """1 dakika ve 1 saat continuous aggregate'leri oluştur + backfill."""
    # --- 1) tag_readings_1min: ham veriden türet ---
    op.execute(
        """
        CREATE MATERIALIZED VIEW tag_readings_1min
        WITH (timescaledb.continuous) AS
        SELECT
            tag_id,
            time_bucket(INTERVAL '1 minute', timestamp) AS bucket,
            AVG(value) AS avg_value,
            MIN(value) AS min_value,
            MAX(value) AS max_value,
            STDDEV(value) AS stddev_value,
            MAX(quality_flag) AS max_quality,
            COUNT(*) AS sample_count
        FROM tag_readings
        GROUP BY tag_id, bucket
        WITH NO DATA;
        """
    )
    op.execute(
        """
        SELECT add_continuous_aggregate_policy(
            'tag_readings_1min',
            start_offset => INTERVAL '3 hours',
            end_offset => INTERVAL '1 minute',
            schedule_interval => INTERVAL '5 minutes'
        );
        """
    )
    op.execute(
        "SELECT add_retention_policy('tag_readings_1min', INTERVAL '3 years');"
    )

    # --- 2) tag_readings_1hour: 1min agregatından türet (hierarchical) ---
    # Hierarchical CA için TimescaleDB policy refresh penceresi hizalama
    # kontrolü yapar; 1min CA'ya invalidation trigger kurulmuş olmalı ki
    # 1hour ondan türetilebilsin — CREATE'in kendisi bu ayarı yapar.
    op.execute(
        """
        CREATE MATERIALIZED VIEW tag_readings_1hour
        WITH (timescaledb.continuous) AS
        SELECT
            tag_id,
            time_bucket(INTERVAL '1 hour', bucket) AS bucket,
            AVG(avg_value) AS avg_value,
            MIN(min_value) AS min_value,
            MAX(max_value) AS max_value,
            AVG(stddev_value) AS stddev_value,
            MAX(max_quality) AS max_quality,
            SUM(sample_count) AS sample_count
        FROM tag_readings_1min
        GROUP BY tag_id, time_bucket(INTERVAL '1 hour', bucket)
        WITH NO DATA;
        """
    )
    op.execute(
        """
        SELECT add_continuous_aggregate_policy(
            'tag_readings_1hour',
            start_offset => INTERVAL '1 day',
            end_offset => INTERVAL '1 hour',
            schedule_interval => INTERVAL '30 minutes'
        );
        """
    )
    # tag_readings_1hour için retention YOK — sınırsız saklama.


def downgrade() -> None:
    """Policy'leri kaldır, agregatları DROP et.

    Sıra önemli: önce 1hour (1min'e bağımlı), sonra 1min. Policy kaldırma
    ``if_exists => true`` ile idempotent.
    """
    # --- 1hour önce (1min'e bağımlı) ---
    op.execute(
        """
        SELECT remove_continuous_aggregate_policy(
            'tag_readings_1hour', if_exists => true
        );
        """
    )
    op.execute(
        "SELECT remove_retention_policy('tag_readings_1hour', if_exists => true);"
    )
    op.execute("DROP MATERIALIZED VIEW IF EXISTS tag_readings_1hour;")

    # --- 1min sonra ---
    op.execute(
        """
        SELECT remove_continuous_aggregate_policy(
            'tag_readings_1min', if_exists => true
        );
        """
    )
    op.execute(
        "SELECT remove_retention_policy('tag_readings_1min', if_exists => true);"
    )
    op.execute("DROP MATERIALIZED VIEW IF EXISTS tag_readings_1min;")
