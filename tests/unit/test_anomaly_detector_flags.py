"""AnomalyDetector master/per-instance flag respect testleri (R-04).

Migration 034 ile ``retention_config.ml_inference_enabled`` (global) ve
``asset_instances.ml_enabled`` (per-instance) flag'leri eklendi.
``_detect_cycle`` her ikisini de tick başında okur ve uygun davranır:

- Global False → tick erken döner (hiç inference yok)
- Global True + instance False → instance atlanır

DB tamamen mock; gerçek scikit-learn fit yok (model dict boşken inference
zaten yapılmaz; testler flag respect path'lerini doğrular).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from custos.analytics.anomaly_detector import AnomalyDetector
from custos.shared.database import AssetInstance, RetentionConfig


def _make_instance(instance_id: int, *, ml_enabled: bool = True) -> AssetInstance:
    return AssetInstance(
        id=instance_id,
        template_id=1,
        name=f"asset-{instance_id}",
        ml_enabled=ml_enabled,
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
async def test_detect_cycle_skips_when_global_disabled(tmp_path: Path) -> None:
    """ml_inference_enabled=False → tick hiç instance dolaşmaz."""
    db = MagicMock()
    db.get_retention_config = AsyncMock(
        return_value=_retention(ml_inference_enabled=False)
    )
    db.list_asset_instances = AsyncMock()  # çağrılmamalı

    detector = AnomalyDetector(db=db, models_dir=tmp_path)
    # Boş olmayan bir model haritası ver — yoksa fonksiyon None
    # döner ve global flag check'e ulaşamaz (test geçersiz olur).
    detector._models[1] = MagicMock()

    await detector._detect_cycle()

    db.get_retention_config.assert_awaited_once()
    db.list_asset_instances.assert_not_awaited()


@pytest.mark.asyncio
async def test_detect_cycle_skips_instance_when_per_instance_disabled(
    tmp_path: Path,
) -> None:
    """instance.ml_enabled=False → o instance için binding bile sorgulanmaz."""
    db = MagicMock()
    db.get_retention_config = AsyncMock(return_value=_retention())
    db.list_asset_instances = AsyncMock(
        return_value=[_make_instance(1, ml_enabled=False)]
    )
    db.list_tag_bindings = AsyncMock()  # çağrılmamalı

    detector = AnomalyDetector(db=db, models_dir=tmp_path)
    detector._models[1] = MagicMock()

    await detector._detect_cycle()

    db.list_asset_instances.assert_awaited_once()
    db.list_tag_bindings.assert_not_awaited()


@pytest.mark.asyncio
async def test_detect_cycle_processes_when_both_flags_on(
    tmp_path: Path,
) -> None:
    """Global+per-instance açık ise instance işlenir (binding sorgulanır)."""
    db = MagicMock()
    db.get_retention_config = AsyncMock(return_value=_retention())
    db.list_asset_instances = AsyncMock(
        return_value=[_make_instance(1, ml_enabled=True)]
    )
    # binding boş — feature vector doldurulmaz, insert_anomaly_score
    # çağrılmaz; ama list_tag_bindings'in çağrıldığını görmek yeterli.
    db.list_tag_bindings = AsyncMock(return_value=[])

    detector = AnomalyDetector(db=db, models_dir=tmp_path)
    detector._models[1] = MagicMock()

    await detector._detect_cycle()

    db.list_tag_bindings.assert_awaited_once_with(1)
