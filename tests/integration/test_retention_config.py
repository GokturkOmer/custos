"""retention_config DB API + TimescaleDB policy senkronizasyon testleri.

F11 Paket F — singleton tablo, get_retention_config/update_retention_config,
ve TimescaleDB retention policy'nin auto_clean_enabled ile tutarlı olması.
"""

from __future__ import annotations

import json
from typing import Any, cast

import pytest

from custos.shared.database import TimescaleDBDatabase


async def _policy_drop_after(
    db: TimescaleDBDatabase,
    hypertable: str,
) -> str | None:
    """timescaledb_information.jobs'dan ``drop_after`` config değerini okur.

    Policy yoksa None döner. Bu test yardımcısı migration 024'teki metadata
    sorgusuyla aynı şekle sahip.
    """
    pool = db._get_pool()
    async with pool.acquire() as conn:
        raw = await conn.fetchval(
            "SELECT config FROM timescaledb_information.jobs "
            "WHERE proc_name = 'policy_retention' "
            "  AND hypertable_name = $1",
            hypertable,
        )
    if raw is None:
        return None
    if isinstance(raw, dict):
        return cast(str, raw.get("drop_after"))
    if isinstance(raw, str):
        return cast(str, json.loads(raw).get("drop_after"))
    return None


@pytest.fixture(autouse=True)
async def _restore_defaults(
    _check_db_available: Any,
    db: TimescaleDBDatabase,
) -> Any:
    """Her test sonrası 365 gün + auto-clean açık varsayılanına döner.

    TimescaleDB policy state'i globaldır; test birbirini bozmasın.
    """
    yield
    await db.update_retention_config(
        raw_retention_days=365,
        auto_clean_enabled=True,
        updated_by="test_restore",
    )


@pytest.mark.usefixtures("_check_db_available")
async def test_get_default_config_365_days(db: TimescaleDBDatabase) -> None:
    """Migration 026 singleton satırını 365 gün + auto-clean açık olarak kurar."""
    cfg = await db.get_retention_config()
    assert cfg.raw_retention_days == 365
    assert cfg.auto_clean_enabled is True


@pytest.mark.usefixtures("_check_db_available")
async def test_update_retention_syncs_timescale_policy(
    db: TimescaleDBDatabase,
) -> None:
    """raw_retention_days=60 yapınca policy drop_after='60 days' olmalı."""
    cfg = await db.update_retention_config(
        raw_retention_days=60,
        auto_clean_enabled=True,
        updated_by="test",
    )
    assert cfg.raw_retention_days == 60
    assert cfg.auto_clean_enabled is True

    drop_after = await _policy_drop_after(db, "tag_readings")
    assert drop_after == "60 days", f"tag_readings retention policy güncellenmedi: {drop_after!r}"
    drop_after_feat = await _policy_drop_after(db, "features")
    assert drop_after_feat == "60 days", (
        f"features retention policy güncellenmedi: {drop_after_feat!r}"
    )


@pytest.mark.usefixtures("_check_db_available")
async def test_auto_clean_off_removes_policy(db: TimescaleDBDatabase) -> None:
    """auto_clean_enabled=False iken iki hypertable'ın policy'si kaldırılmalı."""
    cfg = await db.update_retention_config(
        auto_clean_enabled=False,
        updated_by="test",
    )
    assert cfg.auto_clean_enabled is False

    assert await _policy_drop_after(db, "tag_readings") is None
    assert await _policy_drop_after(db, "features") is None


@pytest.mark.usefixtures("_check_db_available")
async def test_auto_clean_on_reinstates_policy(
    db: TimescaleDBDatabase,
) -> None:
    """Auto-clean'i kapatıp tekrar açınca policy yeni aralıkla geri gelmeli."""
    await db.update_retention_config(
        auto_clean_enabled=False,
        updated_by="test",
    )
    assert await _policy_drop_after(db, "tag_readings") is None

    cfg = await db.update_retention_config(
        raw_retention_days=180,
        auto_clean_enabled=True,
        updated_by="test",
    )
    assert cfg.raw_retention_days == 180
    assert cfg.auto_clean_enabled is True

    drop_after = await _policy_drop_after(db, "tag_readings")
    assert drop_after == "180 days"


@pytest.mark.usefixtures("_check_db_available")
async def test_update_rejects_non_positive_days(
    db: TimescaleDBDatabase,
) -> None:
    """raw_retention_days <= 0 ValueError atmalı (defensive check)."""
    with pytest.raises(ValueError, match="pozitif"):
        await db.update_retention_config(raw_retention_days=0)
