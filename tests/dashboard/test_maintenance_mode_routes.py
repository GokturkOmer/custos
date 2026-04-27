"""Bakım modu (P-04 / V11-104) dashboard route + UI testleri.

Auth bypass conftest tarafından autouse fixture ile uygulanır
(``_fake_dev_session`` developer rolünde döner). Operator-only kontrolü için
ayrıca explicit override ile operatör session enjekte edilir (K2).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from custos.__main__ import app
from custos.analytics.dashboard.auth_dependencies import (
    require_developer,
    require_operator,
)
from custos.shared.database import (
    AssetInstance,
    AssetTemplate,
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
    return _OPERATOR_SESSION


def _fake_retention(global_active: bool = False) -> RetentionConfig:
    """Test için RetentionConfig dataclass'ı üretir."""
    if global_active:
        return RetentionConfig(
            raw_retention_days=365,
            auto_clean_enabled=True,
            updated_at=datetime.now(UTC),
            updated_by="test",
            push_global_enabled=True,
            global_maintenance_until=datetime.now(UTC) + timedelta(hours=2),
            global_maintenance_reason="Test global bakım",
            global_maintenance_started_by_user_id=1,
            global_maintenance_started_at=datetime.now(UTC),
        )
    return RetentionConfig(
        raw_retention_days=365,
        auto_clean_enabled=True,
        updated_at=datetime.now(UTC),
        updated_by="test",
        push_global_enabled=True,
    )


@pytest.fixture
def _mock_db_with_global_active(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """request.app.state.db'yi global maintenance aktif olacak şekilde mock'lar."""
    mock = MagicMock()
    mock.health_check = AsyncMock(return_value=True)
    mock.list_tags = AsyncMock(return_value=[])
    mock.list_asset_instances = AsyncMock(return_value=[])
    mock.list_asset_templates = AsyncMock(return_value=[])
    mock.list_push_subscriptions = AsyncMock(return_value=[])
    mock.get_retention_config = AsyncMock(return_value=_fake_retention(True))
    monkeypatch.setattr(app.state, "db", mock, raising=False)
    monkeypatch.setattr(app.state, "kpi_engine", None, raising=False)
    monkeypatch.setattr(app.state, "anomaly_detector", None, raising=False)
    monkeypatch.setattr(app.state, "archiver", None, raising=False)
    monkeypatch.setattr(app.state, "disk_monitor", None, raising=False)
    monkeypatch.setattr(app.state, "avm_template_pack", {}, raising=False)
    return mock


def test_operator_can_start_global_maintenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """K2: Operator rolü global bakım başlatabilir (require_operator route'u)."""
    mock = MagicMock()
    mock.update_global_maintenance = AsyncMock(return_value=_fake_retention(True))
    mock.get_retention_config = AsyncMock(return_value=_fake_retention(False))
    mock.insert_audit_log = AsyncMock()
    monkeypatch.setattr(app.state, "db", mock, raising=False)

    # Operator rolü ile override; conftest developer veriyordu, içeride
    # require_operator iki rolü de kabul ediyor (K2 — pratik tercih).
    app.dependency_overrides[require_operator] = _fake_op_session
    app.dependency_overrides[require_developer] = _fake_op_session
    try:
        response = client.post(
            "/dashboard/maintenance/global/start",
            data={"duration": "1h", "reason": "K2 operator test"},
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.pop(require_operator, None)
        app.dependency_overrides.pop(require_developer, None)

    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard/settings"
    mock.update_global_maintenance.assert_awaited()


def test_global_banner_renders_when_active(
    _mock_db_with_global_active: MagicMock,
) -> None:
    """Global maintenance aktifken base.html üst banner'ı render edilir."""
    response = client.get("/dashboard/settings")
    assert response.status_code == 200
    text = response.text
    # base.html banner içeriği
    assert "Sistem bakım modu aktif" in text
    # settings.html "Sistem Bakım Modu" kart başlığı
    assert "Sistem Bakım Modu" in text
    # Sebep field'ı render edilmiş
    assert "Test global bakım" in text


def test_remaining_time_displayed_correctly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Per-instance bakım kartında bitiş zamanı (dd/mm HH:MM) render edilir."""
    until = datetime(2026, 12, 31, 18, 30, tzinfo=UTC)
    instance = AssetInstance(
        id=99,
        template_id=1,
        name="Chiller-1",
        description="",
        location="A1",
        status="active",
        maintenance_mode_until=until,
        maintenance_reason="filtre değişimi",
        maintenance_started_by_user_id=1,
        maintenance_started_at=datetime.now(UTC),
    )
    template = AssetTemplate(
        id=1,
        slug="chiller",
        name="Chiller Şablonu",
        description="",
        icon="cpu",
        roles=[],
        kpi_definitions=[],
    )

    mock = MagicMock()
    mock.get_asset_instance = AsyncMock(return_value=instance)
    mock.get_asset_template = AsyncMock(return_value=template)
    mock.list_tag_bindings = AsyncMock(return_value=[])
    mock.get_latest_tag_readings = AsyncMock(return_value={})
    mock.get_latest_kpi_results = AsyncMock(return_value={})
    mock.get_retention_config = AsyncMock(return_value=_fake_retention(False))
    monkeypatch.setattr(app.state, "db", mock, raising=False)

    response = client.get("/dashboard/processes/99")
    assert response.status_code == 200
    text = response.text
    # Per-instance bakım banner
    assert "Bu asset bakım modunda" in text
    # Tarih formatı: 31/12 18:30
    assert "31/12" in text
    assert "filtre değişimi" in text
