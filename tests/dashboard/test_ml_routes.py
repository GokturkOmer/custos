"""ML hub (R-04) dashboard route + helper testleri.

Tüm sayfa + 4 POST endpoint'i developer-only (V11-101). Operator role'üne
override edildiğinde her biri 403 döner. Helper'lar (``_compute_ml_summary``,
``_compute_ml_instance_rows``) saf — mock DB + tmp_path ile birim testi.

Eğitim/reset/toggle endpoint'lerinde ``train_model_for_instance`` patch
edilir (gerçek scikit-learn fit çalıştırılmaz; sadece return path test
edilir). Disk I/O için ``tmp_path`` ile model dizini override edilir.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException, status
from fastapi.testclient import TestClient

from custos.__main__ import app
from custos.analytics.dashboard.app import (
    _compute_ml_instance_rows,
    _compute_ml_summary,
    _ml_model_path,
)
from custos.analytics.dashboard.auth_dependencies import (
    require_developer,
    require_operator,
)
from custos.shared.database import (
    LABEL_CLASS_VALUES,
    AnomalyScore,
    AssetInstance,
    RetentionConfig,
    Session,
)

client = TestClient(app)

_FAR_FUTURE = datetime(2099, 1, 1, tzinfo=UTC)
_OPERATOR_SESSION = Session(
    id=2,
    user_id=42,
    username="test_operator",
    role="operator",
    enabled=True,
    must_change_password=False,
    expires_at=_FAR_FUTURE,
)


def _fake_op_session() -> Session:
    """``require_operator`` override için."""
    return _OPERATOR_SESSION


def _operator_blocked_for_developer() -> Session:
    """``require_developer`` override — operator session 403 fırlatır.

    Knowledge route testlerindeki desenle aynı: FastAPI dependency_overrides
    orijinal fonksiyonu yerinden ettiğinden role check'i de override
    fonksiyonunda yapılır.
    """
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Bu işlem için Geliştirici yetkisi gereklidir",
    )


def _make_instance(
    instance_id: int,
    name: str = "asset",
    *,
    ml_enabled: bool = True,
) -> AssetInstance:
    return AssetInstance(
        id=instance_id,
        template_id=1,
        name=name,
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


# --- Unit: helper fonksiyonları --------------------------------------------


@pytest.mark.asyncio
async def test_compute_ml_summary_no_models(tmp_path: Path) -> None:
    """Hiç model yok — modeled_count=0, last_train_at None."""
    instances = [_make_instance(1), _make_instance(2)]
    db = MagicMock()
    db.count_anomalies = AsyncMock(return_value=0)
    db.get_retention_config = AsyncMock(return_value=_retention())

    summary = await _compute_ml_summary(db, instances, tmp_path)

    assert summary["total_instances"] == 2
    assert summary["modeled_count"] == 0
    assert summary["modeled_pct"] == 0
    assert summary["ml_enabled_count"] == 2
    assert summary["last_train_at"] is None
    assert summary["last_train_age_days"] is None
    assert summary["inference_enabled"] is True


@pytest.mark.asyncio
async def test_compute_ml_summary_with_models(tmp_path: Path) -> None:
    """2 instance, 1 model dosyası — modeled_pct=50, last_train_at dolu."""
    instances = [_make_instance(1), _make_instance(2, ml_enabled=False)]
    # Sadece instance 1 için joblib oluştur
    (tmp_path / "anomaly_1.joblib").write_bytes(b"fake")

    db = MagicMock()
    db.count_anomalies = AsyncMock(return_value=5)
    db.get_retention_config = AsyncMock(return_value=_retention(ml_inference_enabled=False))

    summary = await _compute_ml_summary(db, instances, tmp_path)

    assert summary["total_instances"] == 2
    assert summary["modeled_count"] == 1
    assert summary["modeled_pct"] == 50
    # ml_enabled_count: instance 1 True, instance 2 False
    assert summary["ml_enabled_count"] == 1
    assert summary["anomalies_24h"] == 5
    assert summary["last_train_at"] is not None
    assert summary["last_train_age_days"] is not None
    assert summary["inference_enabled"] is False
    # count_anomalies 24h pencere ile çağrılmış olmalı
    db.count_anomalies.assert_awaited_once()
    call_args = db.count_anomalies.await_args
    assert "since" in call_args.kwargs
    delta = datetime.now(UTC) - call_args.kwargs["since"]
    assert timedelta(hours=23, minutes=55) < delta < timedelta(hours=24, minutes=5)


@pytest.mark.asyncio
async def test_compute_ml_instance_rows_with_score(tmp_path: Path) -> None:
    """Her satır has_model + last_score + stale doğru hesaplanır."""
    instances = [_make_instance(1, "chiller"), _make_instance(2, "ahu")]
    # Model 1 var, model 2 yok
    (tmp_path / "anomaly_1.joblib").write_bytes(b"fake_model_data_xyz")

    latest_score = AnomalyScore(
        instance_id=1,
        timestamp=datetime.now(UTC),
        score=0.0123,
        is_anomaly=False,
    )

    async def _get_latest(instance_id: int) -> AnomalyScore | None:
        return latest_score if instance_id == 1 else None

    db = MagicMock()
    db.get_latest_anomaly_score = AsyncMock(side_effect=_get_latest)

    rows = await _compute_ml_instance_rows(db, instances, tmp_path)

    assert len(rows) == 2
    # Instance 1: model var + skor var
    assert rows[0]["instance"].id == 1
    assert rows[0]["has_model"] is True
    assert rows[0]["model_size_kb"] is not None
    assert rows[0]["last_score"] == pytest.approx(0.0123)
    assert rows[0]["last_is_anomaly"] is False
    assert rows[0]["stale"] is False  # az önce yazıldı
    # Instance 2: model yok + skor yok
    assert rows[1]["instance"].id == 2
    assert rows[1]["has_model"] is False
    assert rows[1]["model_size_kb"] is None
    assert rows[1]["last_score"] is None


@pytest.mark.asyncio
async def test_compute_ml_instance_rows_stale_model(tmp_path: Path) -> None:
    """14+ gün önceki model stale=True döner."""
    instances = [_make_instance(1)]
    model_path = tmp_path / "anomaly_1.joblib"
    model_path.write_bytes(b"old")
    # mtime'ı 20 gün öncesine çek
    old_ts = (datetime.now(UTC) - timedelta(days=20)).timestamp()
    import os

    os.utime(model_path, (old_ts, old_ts))

    db = MagicMock()
    db.get_latest_anomaly_score = AsyncMock(return_value=None)

    rows = await _compute_ml_instance_rows(db, instances, tmp_path)
    assert rows[0]["stale"] is True
    assert rows[0]["last_train_age_days"] is not None
    assert rows[0]["last_train_age_days"] >= 14


# --- Integration: dashboard route'ları --------------------------------------


@pytest.fixture
def _ml_db(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Mock DB — list_asset_instances + audit_log + retention_config + anomaly metodları."""
    mock = MagicMock()
    mock.list_asset_instances = AsyncMock(
        return_value=[
            _make_instance(1, "chiller-1"),
            _make_instance(2, "ahu-2", ml_enabled=False),
        ]
    )
    mock.get_asset_instance = AsyncMock(side_effect=lambda iid: _make_instance(iid))
    mock.update_asset_instance = AsyncMock(return_value=_make_instance(1))
    mock.count_anomalies = AsyncMock(return_value=3)
    mock.get_latest_anomaly_score = AsyncMock(return_value=None)
    mock.get_retention_config = AsyncMock(return_value=_retention())
    mock.update_retention_config = AsyncMock(
        return_value=_retention(ml_inference_enabled=False)
    )
    mock.insert_audit_log = AsyncMock()
    # R-05: ML hub etiketleme bölümü _compute_label_summary çağırıyor.
    mock.count_labels_by_class = AsyncMock(
        return_value=dict.fromkeys(LABEL_CLASS_VALUES, 0),
    )
    mock.list_unlabeled_alarms = AsyncMock(return_value=[])
    monkeypatch.setattr(app.state, "db", mock, raising=False)
    return mock


@pytest.fixture
def _ml_models_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Modüller için tmp_path'i app.state.anomaly_detector.models_dir olarak set eder."""
    fake_detector = MagicMock()
    fake_detector.models_dir = tmp_path
    fake_detector._load_models = MagicMock()
    monkeypatch.setattr(app.state, "anomaly_detector", fake_detector, raising=False)
    yield tmp_path


def test_ml_dashboard_developer_returns_200(
    _ml_db: MagicMock,
    _ml_models_dir: Path,
) -> None:
    """GET /dashboard/ml → developer için sayfa render."""
    response = client.get("/dashboard/ml")
    assert response.status_code == 200
    text = response.text
    # Sayfa iskelet
    assert "ML Hub" in text
    assert "Modelli Instance" in text
    # Instance tablosu — her iki instance de listelenmeli
    assert "chiller-1" in text
    assert "ahu-2" in text
    # Faz 3 placeholder + R-05 ile dolan section'lar
    assert 'id="section-labeling"' in text  # R-05 doldurdu
    assert 'id="section-layer1-rules"' in text
    assert 'id="section-mode-aware-spc"' in text
    assert 'id="section-stuck-at-l3"' in text
    assert 'id="section-shadow-mode"' in text
    # R-06/R-07 ve Faz 3 badge'leri hâlâ placeholder
    assert "v1.1 R-06" in text
    assert "v1.1 R-07" in text
    assert "v1.1 Faz 3" in text


def test_ml_dashboard_operator_returns_403(_ml_db: MagicMock) -> None:
    """Operator rolü developer-only sayfaya erişemez (403)."""
    app.dependency_overrides[require_operator] = _fake_op_session
    app.dependency_overrides[require_developer] = _operator_blocked_for_developer
    try:
        response = client.get("/dashboard/ml")
    finally:
        app.dependency_overrides.pop(require_operator, None)
        app.dependency_overrides.pop(require_developer, None)
    assert response.status_code == 403


def test_ml_train_endpoint_operator_403(_ml_db: MagicMock) -> None:
    """POST /ml/instances/.../train developer-only."""
    app.dependency_overrides[require_operator] = _fake_op_session
    app.dependency_overrides[require_developer] = _operator_blocked_for_developer
    try:
        response = client.post(
            "/dashboard/ml/instances/1/train", follow_redirects=False
        )
    finally:
        app.dependency_overrides.pop(require_operator, None)
        app.dependency_overrides.pop(require_developer, None)
    assert response.status_code == 403
    _ml_db.insert_audit_log.assert_not_awaited()


def test_ml_reset_endpoint_developer_deletes_file(
    _ml_db: MagicMock,
    _ml_models_dir: Path,
) -> None:
    """[Resetle] dosyayı siler + audit log."""
    target = _ml_model_path(_ml_models_dir, 1)
    target.write_bytes(b"existing model")
    assert target.exists()

    response = client.post(
        "/dashboard/ml/instances/1/reset", follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard/ml?ok=reset"
    assert not target.exists()
    _ml_db.insert_audit_log.assert_awaited_once()
    audit_call = _ml_db.insert_audit_log.await_args
    audit_entry = audit_call.args[0]
    assert audit_entry.category == "ml"
    assert audit_entry.action == "ml_reset"


def test_ml_toggle_endpoint_flips_value(
    _ml_db: MagicMock,
    _ml_models_dir: Path,
) -> None:
    """[Aç/Kapat] toggle ml_enabled flag'i çevirir + audit log."""
    # _ml_db.get_asset_instance default ml_enabled=True döndürüyor
    response = client.post(
        "/dashboard/ml/instances/1/toggle", follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard/ml?ok=toggled"

    # update_asset_instance ml_enabled=False ile çağrılmış olmalı
    _ml_db.update_asset_instance.assert_awaited_once_with(
        1, {"ml_enabled": False}
    )
    # Audit log düşmüş olmalı
    audit_entry = _ml_db.insert_audit_log.await_args.args[0]
    assert audit_entry.category == "ml"
    assert audit_entry.action == "ml_toggle_instance"
    assert "off" in audit_entry.detail


def test_ml_global_toggle_flips_and_redirects(_ml_db: MagicMock) -> None:
    """Global toggle ml_inference_enabled flag'i çevirir + audit log."""
    response = client.post("/dashboard/ml/global-toggle", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard/ml?ok=global_toggled"

    # update_retention_config ml_inference_enabled=False ile çağrılmış olmalı
    _ml_db.update_retention_config.assert_awaited_once()
    call = _ml_db.update_retention_config.await_args
    assert call.kwargs["ml_inference_enabled"] is False
    # Audit log
    audit_entry = _ml_db.insert_audit_log.await_args.args[0]
    assert audit_entry.category == "ml"
    assert audit_entry.action == "ml_toggle_global"


def test_ml_train_unknown_instance_returns_404(
    _ml_db: MagicMock,
    _ml_models_dir: Path,
) -> None:
    """Var olmayan instance_id → 404."""
    _ml_db.get_asset_instance = AsyncMock(return_value=None)
    response = client.post(
        "/dashboard/ml/instances/9999/train", follow_redirects=False
    )
    assert response.status_code == 404


def test_ml_train_redirects_with_insufficient_data(
    _ml_db: MagicMock,
    _ml_models_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """train_model_for_instance False döndürürse 'train_insufficient_data' redirect."""

    async def _fake_train(**_kwargs: Any) -> bool:
        return False

    # Route içine import edilen sembolü patch ediyoruz (app.py'deki referans).
    monkeypatch.setattr(
        "custos.analytics.dashboard.app.train_model_for_instance",
        _fake_train,
        raising=True,
    )

    response = client.post(
        "/dashboard/ml/instances/1/train", follow_redirects=False
    )
    assert response.status_code == 303
    assert "error=train_insufficient_data" in response.headers["location"]
    # Audit log: ml_train_skipped
    audit_entry = _ml_db.insert_audit_log.await_args.args[0]
    assert audit_entry.action == "ml_train_skipped"


def test_ml_train_success_path(
    _ml_db: MagicMock,
    _ml_models_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Başarılı eğitim ?ok=trained redirect + ml_train audit + detector reload."""

    async def _fake_train(**_kwargs: Any) -> bool:
        return True

    monkeypatch.setattr(
        "custos.analytics.dashboard.app.train_model_for_instance",
        _fake_train,
        raising=True,
    )

    response = client.post(
        "/dashboard/ml/instances/1/train", follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard/ml?ok=trained"
    audit_entry = _ml_db.insert_audit_log.await_args.args[0]
    assert audit_entry.action == "ml_train"
    # Detector cache'i yeniden yüklenmiş olmalı
    detector = app.state.anomaly_detector
    detector._load_models.assert_called_once()
