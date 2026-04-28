"""PP-08 — train_model_for_instance + AnomalyDetector._load_models testleri.

train_model_for_instance edge case'leri:
- Instance bulunamaz → False
- Template bulunamaz → False
- Tag binding yok → False
- Yetersiz veri (min_len < _MIN_TRAINING_ROWS=10) → False
- Yeterli veri → True + .joblib dosyası diskte

AnomalyDetector._load_models:
- Model dizini yoksa boş model dict
- anomaly_<int>.joblib dosyaları yüklenir
- Bozuk filename (anomaly_xyz.joblib) atlanır (int parse hata)
- Diğer .joblib / .toml dosyaları yoksayılır
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import joblib
import numpy as np
import pytest
from sklearn.ensemble import IsolationForest

from custos.analytics.anomaly_detector import (
    AnomalyDetector,
    train_model_for_instance,
)
from custos.shared.database import (
    AssetInstance,
    AssetTemplate,
    TagBinding,
    TagReading,
)


def _instance(iid: int = 1) -> AssetInstance:
    return AssetInstance(template_id=1, name=f"asset-{iid}", id=iid)


def _template() -> AssetTemplate:
    return AssetTemplate(slug="ahu", name="AHU", id=1)


def _readings(tag_id: str, count: int) -> list[TagReading]:
    now = datetime.now(UTC)
    return [
        TagReading(
            timestamp=now - timedelta(minutes=i),
            tag_id=tag_id,
            value=20.0 + (i % 5),
        )
        for i in range(count)
    ]


def _trained_model() -> IsolationForest:
    """Hızlı test için minimal bir IsolationForest."""
    model = IsolationForest(n_estimators=10, contamination=0.05, random_state=42)
    model.fit(np.array([[1.0], [2.0], [3.0], [4.0], [5.0]]))
    return model


# --- train_model_for_instance ---


@pytest.mark.asyncio
async def test_train_returns_false_when_instance_missing(tmp_path: Path) -> None:
    """Instance bulunamazsa training erken döner ve dosya yazmaz."""
    db = MagicMock()
    db.get_asset_instance = AsyncMock(return_value=None)
    out = tmp_path / "anomaly_1.joblib"

    result = await train_model_for_instance(db, instance_id=1, output_path=out)

    assert result is False
    assert not out.exists()


@pytest.mark.asyncio
async def test_train_returns_false_when_template_missing(tmp_path: Path) -> None:
    """Template bulunamazsa training erken döner."""
    db = MagicMock()
    db.get_asset_instance = AsyncMock(return_value=_instance(1))
    db.get_asset_template = AsyncMock(return_value=None)
    out = tmp_path / "anomaly_1.joblib"

    result = await train_model_for_instance(db, instance_id=1, output_path=out)

    assert result is False
    assert not out.exists()


@pytest.mark.asyncio
async def test_train_returns_false_when_no_bindings(tmp_path: Path) -> None:
    """Tag binding yoksa training erken döner."""
    db = MagicMock()
    db.get_asset_instance = AsyncMock(return_value=_instance(1))
    db.get_asset_template = AsyncMock(return_value=_template())
    db.list_tag_bindings = AsyncMock(return_value=[])
    out = tmp_path / "anomaly_1.joblib"

    result = await train_model_for_instance(db, instance_id=1, output_path=out)

    assert result is False
    assert not out.exists()


@pytest.mark.asyncio
async def test_train_returns_false_when_insufficient_data(tmp_path: Path) -> None:
    """Min eğitim satırı (10) altındaki veri training'i False ile bitirir."""
    db = MagicMock()
    db.get_asset_instance = AsyncMock(return_value=_instance(1))
    db.get_asset_template = AsyncMock(return_value=_template())
    db.list_tag_bindings = AsyncMock(
        return_value=[TagBinding(instance_id=1, role_id=1, tag_id="TAG_A")]
    )
    db.query_tag_readings = AsyncMock(return_value=_readings("TAG_A", count=5))
    out = tmp_path / "anomaly_1.joblib"

    result = await train_model_for_instance(db, instance_id=1, output_path=out)

    assert result is False
    assert not out.exists()


@pytest.mark.asyncio
async def test_train_writes_joblib_when_data_sufficient(tmp_path: Path) -> None:
    """Yeterli veri ile training True döner ve .joblib disk'e yazılır."""
    db = MagicMock()
    db.get_asset_instance = AsyncMock(return_value=_instance(1))
    db.get_asset_template = AsyncMock(return_value=_template())
    db.list_tag_bindings = AsyncMock(
        return_value=[
            TagBinding(instance_id=1, role_id=1, tag_id="TAG_A"),
            TagBinding(instance_id=1, role_id=2, tag_id="TAG_B"),
        ]
    )

    async def _readings_side(tag_id: str, *_args: object, **_kw: object) -> list[TagReading]:
        return _readings(tag_id, count=20)

    db.query_tag_readings = AsyncMock(side_effect=_readings_side)

    out = tmp_path / "anomaly_1.joblib"
    result = await train_model_for_instance(db, instance_id=1, output_path=out)

    assert result is True
    assert out.exists()

    # Yüklenen objenin gerçek IsolationForest olduğunu doğrula
    loaded = joblib.load(out)
    assert isinstance(loaded, IsolationForest)


@pytest.mark.asyncio
async def test_train_creates_parent_directory(tmp_path: Path) -> None:
    """output_path.parent yoksa training mkdir(parents=True) ile oluşturur."""
    db = MagicMock()
    db.get_asset_instance = AsyncMock(return_value=_instance(1))
    db.get_asset_template = AsyncMock(return_value=_template())
    db.list_tag_bindings = AsyncMock(
        return_value=[TagBinding(instance_id=1, role_id=1, tag_id="TAG_A")]
    )
    db.query_tag_readings = AsyncMock(return_value=_readings("TAG_A", count=15))

    nested = tmp_path / "deep" / "nested" / "anomaly_1.joblib"
    assert not nested.parent.exists()

    result = await train_model_for_instance(db, instance_id=1, output_path=nested)

    assert result is True
    assert nested.exists()


# --- AnomalyDetector._load_models ---


def test_load_models_empty_when_dir_missing(tmp_path: Path) -> None:
    """Models dizini yoksa loaded_models boş dict."""
    db = MagicMock()
    detector = AnomalyDetector(db=db, models_dir=tmp_path / "nonexistent")
    detector._load_models()
    assert detector.loaded_models == {}


def test_load_models_finds_anomaly_files(tmp_path: Path) -> None:
    """anomaly_<int>.joblib dosyaları instance_id ile yüklenir."""
    model = _trained_model()
    joblib.dump(model, tmp_path / "anomaly_5.joblib")
    joblib.dump(model, tmp_path / "anomaly_42.joblib")

    db = MagicMock()
    detector = AnomalyDetector(db=db, models_dir=tmp_path)
    detector._load_models()

    assert 5 in detector.loaded_models
    assert 42 in detector.loaded_models
    assert len(detector.loaded_models) == 2


def test_load_models_skips_malformed_filename(tmp_path: Path) -> None:
    """Filename'de int parse edilemezse o dosya atlanır, diğerleri yüklenir."""
    model = _trained_model()
    joblib.dump(model, tmp_path / "anomaly_7.joblib")
    joblib.dump(model, tmp_path / "anomaly_xyz.joblib")  # int parse hatalı

    db = MagicMock()
    detector = AnomalyDetector(db=db, models_dir=tmp_path)
    detector._load_models()

    assert 7 in detector.loaded_models
    # 'xyz' int'e çevrilemediği için atlanır
    assert len(detector.loaded_models) == 1


def test_load_models_ignores_non_anomaly_files(tmp_path: Path) -> None:
    """Glob 'anomaly_*.joblib' patterni dışındaki dosyalar yoksayılır."""
    (tmp_path / "other.joblib").write_bytes(b"junk")
    (tmp_path / "config.toml").write_text("[x]")

    db = MagicMock()
    detector = AnomalyDetector(db=db, models_dir=tmp_path)
    detector._load_models()

    assert detector.loaded_models == {}


def test_load_models_resets_existing_dict(tmp_path: Path) -> None:
    """_load_models çağrıldığında önceki yüklü modeller temizlenir."""
    db = MagicMock()
    detector = AnomalyDetector(db=db, models_dir=tmp_path)
    detector._models[99] = MagicMock()  # eski / stale entry

    # Yeni dosya yok → reset sonrası boş olmalı
    detector._load_models()

    assert 99 not in detector.loaded_models
    assert detector.loaded_models == {}
