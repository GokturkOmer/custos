"""trend_alerts tablosu — Wind pivot Faz 2 P0 (yavas yukari trend monitor).

Faz 2 P0 (2026-05-12). ``TrendMonitor`` (``src/custos/analytics/trend_monitor.py``)
EWMA slope esik asimini tespit ettiginde bu tabloya bir kayit yazar. Hoinka/
EnBW prensibi: "trend > spike" — mekanik bearing arizalari haftalar suren
yavas drift ile gelir; threshold cross alarmlardan ayri kanalda saklanir.

Tasarim kararlari
-----------------
- ``current_database()`` guard (038/039 pattern): sadece ``custos_wind``
  DB'de tablo olusur. AVM ``custos`` ve ``custos_endurance`` DB'lerinde
  NO-OP. Yanlislikla AVM'ye uygulansa bile tablo OLUSMAZ — schema
  dokunulmazligi korunur.
- ``IF NOT EXISTS`` idempotent: re-run safe.
- Hypertable YAPILMIYOR — trend alert frekansi dusuk (asset basina gunde
  birkac), kucuk tablo. Chunk overhead'i degmez.
- Index ``(asset_instance_id, timestamp)``: lead-time analizinde
  ``get_trend_alerts_for_event(asset_id, start, end)`` sorgulari icin.
- ``severity`` CHECK ('warn'/'crit'): TrendMonitor sadece bu iki degeri
  uretir; veri butunlugu icin DB seviyesinde kisitlama.
- FK eklenmiyor (``asset_instance_id``): AVM ``asset_instances`` tablosu
  ``custos`` DB'sinde, biz ``custos_wind`` DB'sindeyiz — cross-DB FK
  olamaz. Caller orphan alert yazmadan once instance varligindan emin
  olmalidir (anomaly_detector zaten yapar).

Revision ID: 041
Revises: 040
Create Date: 2026-05-12
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "041"
down_revision: str | None = "040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Migration sadece bu DB adi altinda aktif olur. Diger DB'lerde NO-OP.
_TARGET_DB_NAME = "custos_wind"

_CURRENT_DB_SQL = sa.text("SELECT current_database()")


def _current_db() -> str:
    """Aktif PostgreSQL DB adini dondurur (production guard'i icin)."""
    bind = op.get_bind()
    return str(bind.execute(_CURRENT_DB_SQL).scalar() or "")


def upgrade() -> None:
    """trend_alerts tablosunu olusturur — sadece custos_wind'de.

    Diger DB'lerde NO-OP. Idempotent (IF NOT EXISTS), re-run safe.
    """
    current_db = _current_db()
    if current_db != _TARGET_DB_NAME:
        # NO-OP — production custos veya farkli DB. Sessizce gec.
        print(  # noqa: T201
            f"041_trend_alerts: NO-OP "
            f"(current_database={current_db!r}, target={_TARGET_DB_NAME!r})",
        )
        return

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS trend_alerts (
            id BIGSERIAL PRIMARY KEY,
            asset_instance_id INTEGER NOT NULL,
            timestamp TIMESTAMPTZ NOT NULL,
            current_score DOUBLE PRECISION NOT NULL,
            ewma_slope DOUBLE PRECISION NOT NULL,
            duration_min INTEGER NOT NULL,
            severity VARCHAR(10) NOT NULL DEFAULT 'warn'
                CHECK (severity IN ('warn', 'crit')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_trend_alerts_asset_ts "
        "ON trend_alerts (asset_instance_id, timestamp)",
    )

    print(  # noqa: T201
        f"041_trend_alerts: DB={current_db!r}, tablo + 1 index olusturuldu",
    )


def downgrade() -> None:
    """trend_alerts tablosunu kaldirir — sadece custos_wind'de.

    Diger DB'lerde NO-OP (upgrade hicbir sey olusturmadi).
    """
    current_db = _current_db()
    if current_db != _TARGET_DB_NAME:
        print(  # noqa: T201
            f"041_trend_alerts downgrade: NO-OP "
            f"(current_database={current_db!r})",
        )
        return

    op.execute("DROP INDEX IF EXISTS ix_trend_alerts_asset_ts")
    op.execute("DROP TABLE IF EXISTS trend_alerts")
