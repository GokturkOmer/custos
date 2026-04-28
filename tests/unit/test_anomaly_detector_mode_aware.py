"""AnomalyDetector mode-aware filter testleri (R-07 / V11-307).

Migration 037 ile ``asset_instances.operating_mode`` (running/startup/
shutdown/idle) eklendi. ``_detect_cycle`` startup ve shutdown modlarinda
alarm yazimini atlar; running ve idle modlarinda normal calisir.

R-04'un ml_enabled flag'i ile orthogonal — her ikisi acik olsa bile
mode startup ya da shutdown ise instance atlanir.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from custos.analytics.anomaly_detector import AnomalyDetector
from custos.shared.database import AssetInstance, RetentionConfig


def _make_instance(
    instance_id: int,
    *,
    ml_enabled: bool = True,
    operating_mode: str = "running",
) -> AssetInstance:
    return AssetInstance(
        id=instance_id,
        template_id=1,
        name=f"asset-{instance_id}",
        ml_enabled=ml_enabled,
        operating_mode=operating_mode,
    )


def _retention(*, ml_inference_enabled: bool = True) -> RetentionConfig:
    return RetentionConfig(
        raw_retention_days=365,
        auto_clean_enabled=True,
        updated_at=datetime.now(UTC),
        updated_by="test",
        ml_inference_enabled=ml_inference_enabled,
    )


@pytest.mark.asyncio
async def test_detect_cycle_skips_startup_mode(tmp_path: Path) -> None:
    """instance.operating_mode='startup' → o instance icin binding sorgusu yapilmaz."""
    db = MagicMock()
    db.get_retention_config = AsyncMock(return_value=_retention())
    db.list_asset_instances = AsyncMock(
        return_value=[_make_instance(1, operating_mode="startup")]
    )
    db.list_tag_bindings = AsyncMock()  # cagrilmamali

    detector = AnomalyDetector(db=db, models_dir=tmp_path)
    detector._models[1] = MagicMock()

    await detector._detect_cycle()

    db.list_asset_instances.assert_awaited_once()
    db.list_tag_bindings.assert_not_awaited()


@pytest.mark.asyncio
async def test_detect_cycle_skips_shutdown_mode(tmp_path: Path) -> None:
    """instance.operating_mode='shutdown' → atlanir (alarm bombardimanini engeller)."""
    db = MagicMock()
    db.get_retention_config = AsyncMock(return_value=_retention())
    db.list_asset_instances = AsyncMock(
        return_value=[_make_instance(1, operating_mode="shutdown")]
    )
    db.list_tag_bindings = AsyncMock()

    detector = AnomalyDetector(db=db, models_dir=tmp_path)
    detector._models[1] = MagicMock()

    await detector._detect_cycle()

    db.list_tag_bindings.assert_not_awaited()


@pytest.mark.asyncio
async def test_detect_cycle_processes_running_mode(tmp_path: Path) -> None:
    """instance.operating_mode='running' (default) → normal islenir."""
    db = MagicMock()
    db.get_retention_config = AsyncMock(return_value=_retention())
    db.list_asset_instances = AsyncMock(
        return_value=[_make_instance(1, operating_mode="running")]
    )
    db.list_tag_bindings = AsyncMock(return_value=[])

    detector = AnomalyDetector(db=db, models_dir=tmp_path)
    detector._models[1] = MagicMock()

    await detector._detect_cycle()

    # Running mode'da binding sorgulanir (binding bos olsa da cagri yapilir).
    db.list_tag_bindings.assert_awaited_once_with(1)


@pytest.mark.asyncio
async def test_detect_cycle_processes_idle_mode(tmp_path: Path) -> None:
    """instance.operating_mode='idle' → islenir (sicaklik drift'i fark edilsin)."""
    db = MagicMock()
    db.get_retention_config = AsyncMock(return_value=_retention())
    db.list_asset_instances = AsyncMock(
        return_value=[_make_instance(1, operating_mode="idle")]
    )
    db.list_tag_bindings = AsyncMock(return_value=[])

    detector = AnomalyDetector(db=db, models_dir=tmp_path)
    detector._models[1] = MagicMock()

    await detector._detect_cycle()

    db.list_tag_bindings.assert_awaited_once_with(1)
