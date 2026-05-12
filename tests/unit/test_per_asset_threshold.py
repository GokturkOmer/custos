"""src/custos/analytics/per_asset_threshold.py — birim testleri (Faz 2 Prompt 2).

Kapsam:
- Env-gate (``resolve_enabled``) tum kabul-edilebilir degerleri tanir.
- ``default_quantile`` AE/IF iyi, bilinmeyen engine → ValueError.
- ``is_anomaly_at_threshold`` yon konvansiyonu (AE ust kuyruk, IF alt).
- Calibrator disabled iken: calibrate RuntimeError, get_threshold None,
  warm_cache no-op (0).
- Calibrator enabled: warm_cache cache doldurur, get_threshold senkron
  okur, cache miss → None.
- ``calibrate``: dogru quantile, DB upsert + cache guncellemesi,
  invalid input erken yakalanir (engine_type, scores 1D olmali,
  yeterli sample, finite, quantile aralik).
- ``invalidate`` / ``invalidate_all`` cache temizler.
- DB ``upsert_asset_threshold`` cagrilan sirayla mock'la dogrulanir.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from custos.analytics.per_asset_threshold import (
    DEFAULT_QUANTILE_AE,
    DEFAULT_QUANTILE_IF,
    MIN_TRAINING_SCORES,
    PerAssetThresholdCalibrator,
    default_quantile,
    is_anomaly_at_threshold,
    resolve_enabled,
)
from custos.shared.database import AssetThreshold


def _record(
    *,
    asset_instance_id: int = 1,
    engine_type: str = "ae",
    threshold: float = 0.42,
    sample_count: int = 100,
) -> AssetThreshold:
    """Test fixture'i — DB'den donen kayit simulasyonu."""
    return AssetThreshold(
        asset_instance_id=asset_instance_id,
        engine_type=engine_type,
        threshold=threshold,
        training_quantile=DEFAULT_QUANTILE_AE,
        sample_count=sample_count,
        calibrated_at=datetime.now(UTC),
        id=10,
    )


# ---------------- Env-gate ----------------


def test_resolve_enabled_default_is_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """Env ayarsiz → False (AVM safe-default)."""
    monkeypatch.delenv("CUSTOS_PER_ASSET_THRESHOLD", raising=False)
    assert resolve_enabled() is False


@pytest.mark.parametrize("raw", ["on", "ON", "1", "true", "yes", "  On  "])
def test_resolve_enabled_truthy_values(
    monkeypatch: pytest.MonkeyPatch, raw: str,
) -> None:
    """on/1/true/yes (case + whitespace tolerant) → True."""
    monkeypatch.setenv("CUSTOS_PER_ASSET_THRESHOLD", raw)
    assert resolve_enabled() is True


@pytest.mark.parametrize("raw", ["off", "0", "no", "false", "foo", ""])
def test_resolve_enabled_falsy_values(
    monkeypatch: pytest.MonkeyPatch, raw: str,
) -> None:
    """Bilinmeyen veya 'off' → False."""
    monkeypatch.setenv("CUSTOS_PER_ASSET_THRESHOLD", raw)
    assert resolve_enabled() is False


# ---------------- default_quantile ----------------


def test_default_quantile_ae() -> None:
    """AE → 0.99 (ust kuyruk)."""
    assert default_quantile("ae") == DEFAULT_QUANTILE_AE


def test_default_quantile_if() -> None:
    """IF → 0.01 (alt kuyruk)."""
    assert default_quantile("if") == DEFAULT_QUANTILE_IF


def test_default_quantile_unknown_raises() -> None:
    """Bilinmeyen engine_type → ValueError."""
    with pytest.raises(ValueError, match="engine_type"):
        default_quantile("unknown")


# ---------------- is_anomaly_at_threshold ----------------


def test_is_anomaly_ae_score_above_threshold() -> None:
    """AE: score > threshold → True (ust kuyruk)."""
    assert is_anomaly_at_threshold(0.5, "ae", 0.4) is True


def test_is_anomaly_ae_score_at_or_below_threshold() -> None:
    """AE: score <= threshold → False."""
    assert is_anomaly_at_threshold(0.4, "ae", 0.4) is False
    assert is_anomaly_at_threshold(0.3, "ae", 0.4) is False


def test_is_anomaly_if_score_below_threshold() -> None:
    """IF: score < threshold → True (alt kuyruk)."""
    assert is_anomaly_at_threshold(-0.3, "if", -0.1) is True


def test_is_anomaly_if_score_at_or_above_threshold() -> None:
    """IF: score >= threshold → False."""
    assert is_anomaly_at_threshold(-0.1, "if", -0.1) is False
    assert is_anomaly_at_threshold(0.1, "if", -0.1) is False


def test_is_anomaly_unknown_engine_raises() -> None:
    """Bilinmeyen engine_type → ValueError."""
    with pytest.raises(ValueError, match="engine_type"):
        is_anomaly_at_threshold(0.5, "unknown", 0.4)


# ---------------- Calibrator disabled state ----------------


def test_disabled_calibrator_warm_cache_is_noop() -> None:
    """enabled=False → warm_cache no-op, DB cagrisi yok."""
    db = MagicMock()
    db.list_asset_thresholds = AsyncMock()
    cal = PerAssetThresholdCalibrator(db=db, enabled=False)
    assert cal.enabled is False
    import asyncio
    count = asyncio.run(cal.warm_cache())
    assert count == 0
    db.list_asset_thresholds.assert_not_awaited()


def test_disabled_calibrator_get_threshold_returns_none() -> None:
    """enabled=False → get_threshold hep None."""
    db = MagicMock()
    cal = PerAssetThresholdCalibrator(db=db, enabled=False)
    assert cal.get_threshold(1, "ae") is None
    assert cal.get_threshold(99, "if") is None


@pytest.mark.asyncio
async def test_disabled_calibrator_calibrate_raises() -> None:
    """enabled=False iken calibrate → RuntimeError (sessizce yutmak hata gizler)."""
    db = MagicMock()
    db.upsert_asset_threshold = AsyncMock()
    cal = PerAssetThresholdCalibrator(db=db, enabled=False)
    with pytest.raises(RuntimeError, match="disabled"):
        await cal.calibrate(
            asset_instance_id=1,
            engine_type="ae",
            training_scores=np.linspace(0.0, 1.0, 100),
        )
    db.upsert_asset_threshold.assert_not_awaited()


# ---------------- Calibrator enabled state ----------------


@pytest.mark.asyncio
async def test_warm_cache_populates_from_db() -> None:
    """enabled=True warm_cache DB list'i alip cache'e doldurur."""
    db = MagicMock()
    records = [
        _record(asset_instance_id=1, engine_type="ae", threshold=0.42),
        _record(asset_instance_id=1, engine_type="if", threshold=-0.31),
        _record(asset_instance_id=2, engine_type="ae", threshold=0.55),
    ]
    db.list_asset_thresholds = AsyncMock(return_value=records)
    cal = PerAssetThresholdCalibrator(db=db, enabled=True)
    count = await cal.warm_cache()
    assert count == 3
    assert cal.cache_size == 3
    assert cal.get_threshold(1, "ae") == pytest.approx(0.42)
    assert cal.get_threshold(1, "if") == pytest.approx(-0.31)
    assert cal.get_threshold(2, "ae") == pytest.approx(0.55)


@pytest.mark.asyncio
async def test_get_threshold_cache_miss_returns_none() -> None:
    """Cache'te olmayan asset+engine → None (caller fallback'e geceer)."""
    db = MagicMock()
    db.list_asset_thresholds = AsyncMock(return_value=[
        _record(asset_instance_id=1, engine_type="ae"),
    ])
    cal = PerAssetThresholdCalibrator(db=db, enabled=True)
    await cal.warm_cache()
    # Cache'te (1, 'ae') var ama (1, 'if') yok
    assert cal.get_threshold(1, "if") is None
    # Yepyeni asset
    assert cal.get_threshold(999, "ae") is None


# ---------------- calibrate behavior ----------------


@pytest.mark.asyncio
async def test_calibrate_ae_uses_default_high_quantile() -> None:
    """AE default quantile 0.99 — ust %1 esige denk."""
    db = MagicMock()
    captured: list[AssetThreshold] = []

    async def _capture(arg: AssetThreshold) -> AssetThreshold:
        captured.append(arg)
        # DB'nin atadigi id + timestamp simulasyonu
        return AssetThreshold(
            asset_instance_id=arg.asset_instance_id,
            engine_type=arg.engine_type,
            threshold=arg.threshold,
            training_quantile=arg.training_quantile,
            sample_count=arg.sample_count,
            calibrated_at=arg.calibrated_at,
            id=42,
        )

    db.upsert_asset_threshold = AsyncMock(side_effect=_capture)
    cal = PerAssetThresholdCalibrator(db=db, enabled=True)
    scores = np.linspace(0.0, 1.0, 1000)
    result = await cal.calibrate(
        asset_instance_id=5,
        engine_type="ae",
        training_scores=scores,
    )
    assert result.engine_type == "ae"
    assert result.training_quantile == pytest.approx(DEFAULT_QUANTILE_AE)
    # 0-1 lineer dagilim, quantile(0.99) ~ 0.99
    assert result.threshold == pytest.approx(0.99, abs=0.01)
    assert result.sample_count == 1000
    assert len(captured) == 1
    # Cache de guncellenmis olmali
    assert cal.get_threshold(5, "ae") == pytest.approx(0.99, abs=0.01)


@pytest.mark.asyncio
async def test_calibrate_if_uses_default_low_quantile() -> None:
    """IF default quantile 0.01 — alt %1 esige denk."""
    db = MagicMock()
    db.upsert_asset_threshold = AsyncMock(
        side_effect=lambda x: AssetThreshold(
            asset_instance_id=x.asset_instance_id,
            engine_type=x.engine_type,
            threshold=x.threshold,
            training_quantile=x.training_quantile,
            sample_count=x.sample_count,
            calibrated_at=x.calibrated_at,
            id=1,
        ),
    )
    cal = PerAssetThresholdCalibrator(db=db, enabled=True)
    scores = np.linspace(-0.5, 0.5, 1000)
    result = await cal.calibrate(
        asset_instance_id=7,
        engine_type="if",
        training_scores=scores,
    )
    assert result.training_quantile == pytest.approx(DEFAULT_QUANTILE_IF)
    # -0.5 .. 0.5 lineer, quantile(0.01) ~ -0.49
    assert result.threshold == pytest.approx(-0.49, abs=0.01)


@pytest.mark.asyncio
async def test_calibrate_custom_quantile_overrides_default() -> None:
    """Explicit quantile parametresi engine default'unu yener."""
    db = MagicMock()
    db.upsert_asset_threshold = AsyncMock(
        side_effect=lambda x: AssetThreshold(
            asset_instance_id=x.asset_instance_id,
            engine_type=x.engine_type,
            threshold=x.threshold,
            training_quantile=x.training_quantile,
            sample_count=x.sample_count,
        ),
    )
    cal = PerAssetThresholdCalibrator(db=db, enabled=True)
    scores = np.linspace(0.0, 1.0, 1000)
    result = await cal.calibrate(
        asset_instance_id=1,
        engine_type="ae",
        training_scores=scores,
        quantile=0.5,
    )
    assert result.training_quantile == pytest.approx(0.5)
    assert result.threshold == pytest.approx(0.5, abs=0.01)


@pytest.mark.asyncio
async def test_calibrate_invalid_engine_type_raises() -> None:
    """Bilinmeyen engine_type → ValueError (DB cagrisi olmaz)."""
    db = MagicMock()
    db.upsert_asset_threshold = AsyncMock()
    cal = PerAssetThresholdCalibrator(db=db, enabled=True)
    with pytest.raises(ValueError, match="engine_type"):
        await cal.calibrate(
            asset_instance_id=1,
            engine_type="cross_sensor",  # rule-tabanli, threshold yok
            training_scores=np.ones(100),
        )
    db.upsert_asset_threshold.assert_not_awaited()


@pytest.mark.asyncio
async def test_calibrate_too_few_samples_raises() -> None:
    """MIN_TRAINING_SCORES altinda → ValueError."""
    db = MagicMock()
    db.upsert_asset_threshold = AsyncMock()
    cal = PerAssetThresholdCalibrator(db=db, enabled=True)
    with pytest.raises(ValueError, match="Yetersiz"):
        await cal.calibrate(
            asset_instance_id=1,
            engine_type="ae",
            training_scores=np.zeros(MIN_TRAINING_SCORES - 1),
        )
    db.upsert_asset_threshold.assert_not_awaited()


@pytest.mark.asyncio
async def test_calibrate_non_finite_scores_raises() -> None:
    """NaN/inf scores → ValueError (caller temizlemeli)."""
    db = MagicMock()
    db.upsert_asset_threshold = AsyncMock()
    cal = PerAssetThresholdCalibrator(db=db, enabled=True)
    scores = np.linspace(0.0, 1.0, 200)
    scores[10] = np.nan
    with pytest.raises(ValueError, match="NaN/inf"):
        await cal.calibrate(
            asset_instance_id=1,
            engine_type="ae",
            training_scores=scores,
        )


@pytest.mark.asyncio
async def test_calibrate_invalid_quantile_raises() -> None:
    """quantile (0, 1) disinda → ValueError."""
    db = MagicMock()
    db.upsert_asset_threshold = AsyncMock()
    cal = PerAssetThresholdCalibrator(db=db, enabled=True)
    scores = np.linspace(0.0, 1.0, 200)
    with pytest.raises(ValueError, match="quantile"):
        await cal.calibrate(
            asset_instance_id=1,
            engine_type="ae",
            training_scores=scores,
            quantile=0.0,
        )
    with pytest.raises(ValueError, match="quantile"):
        await cal.calibrate(
            asset_instance_id=1,
            engine_type="ae",
            training_scores=scores,
            quantile=1.0,
        )


@pytest.mark.asyncio
async def test_calibrate_2d_scores_raises() -> None:
    """training_scores 1D olmali — 2D → ValueError."""
    db = MagicMock()
    db.upsert_asset_threshold = AsyncMock()
    cal = PerAssetThresholdCalibrator(db=db, enabled=True)
    with pytest.raises(ValueError, match="1D"):
        await cal.calibrate(
            asset_instance_id=1,
            engine_type="ae",
            training_scores=np.zeros((100, 2)),
        )


# ---------------- Cache invalidation ----------------


@pytest.mark.asyncio
async def test_invalidate_removes_single_entry() -> None:
    """invalidate(asset, engine) tek kaydi siler, digerleri kalir."""
    db = MagicMock()
    db.list_asset_thresholds = AsyncMock(return_value=[
        _record(asset_instance_id=1, engine_type="ae"),
        _record(asset_instance_id=1, engine_type="if"),
    ])
    cal = PerAssetThresholdCalibrator(db=db, enabled=True)
    await cal.warm_cache()
    cal.invalidate(1, "ae")
    assert cal.get_threshold(1, "ae") is None
    assert cal.get_threshold(1, "if") is not None


@pytest.mark.asyncio
async def test_invalidate_all_clears_cache() -> None:
    """invalidate_all cache'i komple bosaltir."""
    db = MagicMock()
    db.list_asset_thresholds = AsyncMock(return_value=[
        _record(asset_instance_id=1, engine_type="ae"),
        _record(asset_instance_id=2, engine_type="if"),
    ])
    cal = PerAssetThresholdCalibrator(db=db, enabled=True)
    await cal.warm_cache()
    assert cal.cache_size == 2
    cal.invalidate_all()
    assert cal.cache_size == 0


# ---------------- Env-default initialization ----------------


def test_calibrator_reads_env_when_enabled_not_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Constructor'a enabled verilmezse env okunur."""
    monkeypatch.setenv("CUSTOS_PER_ASSET_THRESHOLD", "on")
    db = MagicMock()
    cal = PerAssetThresholdCalibrator(db=db)  # enabled=None
    assert cal.enabled is True


def test_calibrator_explicit_enabled_overrides_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit enabled=False env 'on' iken bile devre disi."""
    monkeypatch.setenv("CUSTOS_PER_ASSET_THRESHOLD", "on")
    db = MagicMock()
    cal = PerAssetThresholdCalibrator(db=db, enabled=False)
    assert cal.enabled is False
