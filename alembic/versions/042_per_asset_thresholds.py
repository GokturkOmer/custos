"""asset_thresholds tablosu — Wind pivot Faz 2 (per-asset adaptive threshold).

Faz 2 Prompt 2 (2026-05-12). Faz 1 paradoksu: Asset 0 Event 0 (Gen bearing)
1.5 gun ONCE yakalandi, ama Asset 10 Event 40 (ayni ariza tipi) 4.5 gun GEC.
Sebep: global quantile(0.99) threshold asset varyansini gozardi ediyordu.
Bu migration her asset + engine_type icin ayri threshold saklamak amaciyla
asset_thresholds tablosunu olusturur.

Tasarim kararlari
-----------------
- ``current_database()`` guard (038/039/041 pattern): sadece ``custos_wind``
  DB'de tablo olusur. AVM ``custos`` ve ``custos_endurance`` DB'lerinde
  NO-OP. Yanlislikla AVM'ye uygulansa bile tablo OLUSMAZ — schema
  dokunulmazligi korunur.
- ``IF NOT EXISTS`` idempotent: re-run safe.
- ``UNIQUE (asset_instance_id, engine_type)``: her asset + engine bir
  threshold. Re-kalibre edildiginde UPSERT ile mevcut satir guncellenir.
- Hypertable YAPILMIYOR — threshold sayisi cok kucuk (5 turbin x 2 engine
  = 10 satir). Chunk overhead'i degmez.
- FK eklenmiyor (``asset_instance_id``): AVM ``asset_instances`` tablosu
  ``custos`` DB'sinde, biz ``custos_wind`` DB'sindeyiz — cross-DB FK
  olamaz. Caller orphan threshold yazmadan once instance varligindan emin
  olmalidir.
- ``engine_type`` CHECK ('if'/'ae'): cross_sensor rule-tabanlidir,
  threshold scalar degildir; bu tablo sadece IF + AE icin.
- ``training_quantile``: Kalibrasyonda kullanilan quantile (typ. 0.99 AE,
  0.01 IF). Debug + tekrar uretilebilirlik icin saklanir.

Revision ID: 042
Revises: 041
Create Date: 2026-05-12
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "042"
down_revision: str | None = "041"
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
    """asset_thresholds tablosunu olusturur — sadece custos_wind'de.

    Diger DB'lerde NO-OP. Idempotent (IF NOT EXISTS), re-run safe.
    """
    current_db = _current_db()
    if current_db != _TARGET_DB_NAME:
        # NO-OP — production custos veya farkli DB. Sessizce gec.
        print(  # noqa: T201
            f"042_per_asset_thresholds: NO-OP "
            f"(current_database={current_db!r}, target={_TARGET_DB_NAME!r})",
        )
        return

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS asset_thresholds (
            id BIGSERIAL PRIMARY KEY,
            asset_instance_id INTEGER NOT NULL,
            engine_type VARCHAR(20) NOT NULL
                CHECK (engine_type IN ('if', 'ae')),
            threshold DOUBLE PRECISION NOT NULL,
            training_quantile DOUBLE PRECISION NOT NULL,
            sample_count INTEGER NOT NULL DEFAULT 0
                CHECK (sample_count >= 0),
            calibrated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_asset_thresholds_asset_engine
                UNIQUE (asset_instance_id, engine_type)
        )
        """,
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_asset_thresholds_asset "
        "ON asset_thresholds (asset_instance_id)",
    )

    print(  # noqa: T201
        f"042_per_asset_thresholds: DB={current_db!r}, tablo + 1 index olusturuldu",
    )


def downgrade() -> None:
    """asset_thresholds tablosunu kaldirir — sadece custos_wind'de.

    Diger DB'lerde NO-OP (upgrade hicbir sey olusturmadi).
    """
    current_db = _current_db()
    if current_db != _TARGET_DB_NAME:
        print(  # noqa: T201
            f"042_per_asset_thresholds downgrade: NO-OP "
            f"(current_database={current_db!r})",
        )
        return

    op.execute("DROP INDEX IF EXISTS ix_asset_thresholds_asset")
    op.execute("DROP TABLE IF EXISTS asset_thresholds")
