"""Alarm event etiketleme tablosu (R-05 / V11-301).

Operator alarm sayfasinda her alarm/anomaly olayini 4 sinifa atar:

- ``gercek_ariza``    : Gercek ariza (Layer 1 dogru tetikledi)
- ``yanlis_alarm``    : Yanlis alarm (false positive)
- ``bakim_sirasinda`` : Bakim sirasinda olusmus (training set'ten cikar)
- ``bilinmiyor``      : Operator emin degil

Pilot suresince etiketler birikir; pilot kabul sonrasi V11-303 (Shadow mode +
Auto retraining, Faz 3 / P-12) bu etiketleri shadow inference baseline +
otomatik retraining icin kullanir.

UNIQUE (alarm_event_id) — her alarm icin tek aktif etiket. Re-label gerekirse
ON CONFLICT ile mevcut satir update edilir; tarihsel etiket gecmisi audit_log
uzerinden gorulebilir (lux ayri ``label_history`` tablosu acmiyoruz).

ON DELETE CASCADE: alarm_events kaydi silinirse etiket otomatik kalkar.
labeled_by_user_id NULL gecmedigi icin SET NULL kullanmiyoruz; user silinmesi
``users`` tablosundaki RESTRICT ile yumusatilir (zaten user softdelete
yapilmiyor).

Revision ID: 035
Revises: 034
Create Date: 2026-04-28
"""

from collections.abc import Sequence

from alembic import op

revision: str = "035"
down_revision: str | None = "034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """alarm_event_labels tablosunu olusturur."""
    op.execute(
        """
        CREATE TABLE alarm_event_labels (
            id BIGSERIAL PRIMARY KEY,
            alarm_event_id BIGINT NOT NULL
                REFERENCES alarm_events(id) ON DELETE CASCADE,
            label_class TEXT NOT NULL
                CHECK (label_class IN (
                    'gercek_ariza',
                    'yanlis_alarm',
                    'bakim_sirasinda',
                    'bilinmiyor'
                )),
            labeled_by_user_id INTEGER NOT NULL
                REFERENCES users(id),
            labeled_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            notes TEXT NOT NULL DEFAULT '',
            UNIQUE (alarm_event_id)
        );
        """
    )
    # Hub'daki "son 30 gun 4 sinif sayim" sorgusu icin bilesik index;
    # review queue tarafinda label_class filtresi yok ama ilerleyen ML
    # baseline sorgulari icin de etkin (label_class + zaman tarama).
    op.execute(
        """
        CREATE INDEX idx_alarm_event_labels_class_at
            ON alarm_event_labels(label_class, labeled_at DESC);
        """
    )


def downgrade() -> None:
    """alarm_event_labels tablosunu kaldirir."""
    op.execute("DROP TABLE IF EXISTS alarm_event_labels;")
