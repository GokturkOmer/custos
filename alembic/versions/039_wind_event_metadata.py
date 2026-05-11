"""wind_event_metadata yan tablosu (Faz 1.2 — sadece custos_wind DB'sine).

Faz 1.2 (2026-05-12). Fraunhofer CARE replay sirasinda her 10dk'lik
tick icin SCADA metadata'sini (status_type_id, train_test, original
event/asset id'leri) hypertable disinda saklar. AVM production schema
korunur — `tag_readings` hypertable'a kolon eklenmez (JSONB performans
riski + chunk migration karmasi yok).

Tasarim kararlari:
- ``tag_readings`` ile FK YOK: hypertable + chunk seviyesinde FK
  performans sorunu yaratir; ayrica replay disindaki collector
  read'leri zaten metadata uretmez (yan-band).
- JOIN: ``(asset_instance_id, timestamp)`` tuple uzerinden tag_readings
  ile birlestirilir (tolerans pencereli — collector poll gecikmesi
  nedeniyle bit-exact garantisi yok).
- Her 10dk'da **1 satir** (81 reading'e karsi) — kucuk tablo, hypertable
  YAPILMAZ. UNIQUE (asset_instance_id, timestamp) duplicate replay'i
  engeller.
- ``current_database()`` guard (038 ile ayni pattern): production
  ``custos`` veya farkli DB'de NO-OP. Yanlislikla AVM'ye uygulansa bile
  tablo OLUSMAZ.

Revision ID: 039
Revises: 038
Create Date: 2026-05-12
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "039"
down_revision: str | None = "038"
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
    """wind_event_metadata yan tablosunu olusturur — sadece custos_wind'de.

    Diger DB'lerde NO-OP. Indeksler:
    - ``ix_wind_event_meta_asset_ts``: tag_readings JOIN'i icin
      (asset_instance_id, timestamp) compound.
    - ``ix_wind_event_meta_status``: egitim/test filtre icin
      status_type_id uzerinde.
    """
    current_db = _current_db()
    if current_db != _TARGET_DB_NAME:
        # NO-OP — production custos veya farkli DB. Sessizce gec.
        print(  # noqa: T201
            f"039_wind_event_metadata: NO-OP "
            f"(current_database={current_db!r}, target={_TARGET_DB_NAME!r})",
        )
        return

    op.create_table(
        "wind_event_metadata",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("asset_instance_id", sa.Integer, nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        # status_type_id: 0=Normal, 1=Derated, 2=Idling, 3=Service,
        # 4=Downtime, 5=Other (CARE dataset semantik'i).
        sa.Column("status_type_id", sa.SmallInteger, nullable=False),
        # train_test: "train" | "prediction" (CARE dataset onceden ayrilmis).
        sa.Column("train_test", sa.String(20), nullable=False),
        # original_event_id: CSV'deki `id` kolonu (tick sira numarasi);
        # event_info.csv ile birlestirme icin.
        sa.Column("original_event_id", sa.Integer, nullable=True),
        # original_asset_id: CSV'deki `asset_id` (CARE dataset'in dahili id).
        sa.Column("original_asset_id", sa.String(50), nullable=True),
        sa.UniqueConstraint(
            "asset_instance_id",
            "timestamp",
            name="uq_wind_meta_asset_ts",
        ),
    )
    op.create_index(
        "ix_wind_event_meta_asset_ts",
        "wind_event_metadata",
        ["asset_instance_id", "timestamp"],
    )
    op.create_index(
        "ix_wind_event_meta_status",
        "wind_event_metadata",
        ["status_type_id"],
    )

    print(  # noqa: T201
        f"039_wind_event_metadata: DB={current_db!r}, "
        "tablo + 2 index olusturuldu",
    )


def downgrade() -> None:
    """wind_event_metadata tablosunu kaldirir — sadece custos_wind'de.

    Diger DB'lerde NO-OP (upgrade hicbir sey olusturmadi).
    """
    current_db = _current_db()
    if current_db != _TARGET_DB_NAME:
        print(  # noqa: T201
            f"039_wind_event_metadata downgrade: NO-OP "
            f"(current_database={current_db!r})",
        )
        return

    op.drop_index("ix_wind_event_meta_status", table_name="wind_event_metadata")
    op.drop_index("ix_wind_event_meta_asset_ts", table_name="wind_event_metadata")
    op.drop_table("wind_event_metadata")
