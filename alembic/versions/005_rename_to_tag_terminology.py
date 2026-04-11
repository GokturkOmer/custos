"""Terminoloji güncellemesi: raw_readings → tag_readings, sensor_id → tag_id.

Brief v1.4 ile gelen terminoloji değişikliği.

Revision ID: 005
Revises: 004
Create Date: 2026-04-11
"""

from collections.abc import Sequence

from alembic import op

revision: str = "005"
down_revision: str | None = "004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """raw_readings → tag_readings, sensor_id → tag_id rename işlemleri."""
    # raw_readings tablosu → tag_readings
    op.execute("ALTER TABLE raw_readings RENAME TO tag_readings;")
    op.execute("ALTER TABLE tag_readings RENAME COLUMN sensor_id TO tag_id;")
    op.execute(
        "ALTER INDEX idx_raw_readings_sensor_time "
        "RENAME TO idx_tag_readings_tag_time;"
    )

    # features tablosundaki sensor_id → tag_id
    op.execute("ALTER TABLE features RENAME COLUMN sensor_id TO tag_id;")
    op.execute(
        "ALTER INDEX idx_features_sensor_name_time "
        "RENAME TO idx_features_tag_name_time;"
    )


def downgrade() -> None:
    """tag_readings → raw_readings, tag_id → sensor_id geri alma."""
    # features
    op.execute(
        "ALTER INDEX idx_features_tag_name_time "
        "RENAME TO idx_features_sensor_name_time;"
    )
    op.execute("ALTER TABLE features RENAME COLUMN tag_id TO sensor_id;")

    # tag_readings → raw_readings
    op.execute(
        "ALTER INDEX idx_tag_readings_tag_time "
        "RENAME TO idx_raw_readings_sensor_time;"
    )
    op.execute("ALTER TABLE tag_readings RENAME COLUMN tag_id TO sensor_id;")
    op.execute("ALTER TABLE tag_readings RENAME TO raw_readings;")
