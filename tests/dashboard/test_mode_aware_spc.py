"""Mode-aware + SPC dashboard testleri (R-07 / V11-307/308).

Iki kanal:

1. **Mode toggle endpoint** — POST /dashboard/processes/{id}/operating-mode:
   - Gecerli mode -> instance.operating_mode + audit log + 303 redirect
   - Gecersiz mode -> 400
   - Bulunamayan instance -> 404

2. **_compute_mode_spc_summary helper** — ML hub kart icerigini doldurur:
   - Bos durum (hicbir spc_state, mode="running" default)
   - Dolu durum (karisik mode'lar + spc_state'ler)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from custos.__main__ import app
from custos.analytics.dashboard.app import _compute_mode_spc_summary
from custos.analytics.dashboard.auth_dependencies import (
    require_developer,
    require_operator,
)
from custos.shared.database import (
    AlarmEvent,
    AssetInstance,
    AuditLogEntry,
    Session,
    SpcState,
    TagRecord,
)

client = TestClient(app)

_FAR_FUTURE = datetime(2099, 1, 1, tzinfo=UTC)
_OPERATOR_SESSION = Session(
    id=2,
    user_id=42,
    username="test_op",
    role="operator",
    enabled=True,
    must_change_password=False,
    expires_at=_FAR_FUTURE,
)


def _fake_op_session() -> Session:
    return _OPERATOR_SESSION


def _instance(
    instance_id: int = 1,
    *,
    operating_mode: str = "running",
) -> AssetInstance:
    return AssetInstance(
        id=instance_id,
        template_id=1,
        name=f"asset-{instance_id}",
        operating_mode=operating_mode,
    )


# --- Mode toggle endpoint -----------------------------------------------------


def _setup_db_for_toggle(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """``mode toggle endpoint`` icin minimum DB mock'u."""
    mock = MagicMock()
    mock.get_asset_instance = AsyncMock(
        side_effect=lambda iid: _instance(iid, operating_mode="running")
    )
    mock.update_asset_instance = AsyncMock(return_value=_instance(1))
    mock.insert_audit_log = AsyncMock()
    monkeypatch.setattr(app.state, "db", mock, raising=False)
    app.dependency_overrides[require_operator] = _fake_op_session
    app.dependency_overrides[require_developer] = _fake_op_session
    return mock


def test_mode_toggle_valid_mode_updates_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /processes/1/operating-mode startup -> update + audit + 303."""
    db = _setup_db_for_toggle(monkeypatch)
    try:
        response = client.post(
            "/dashboard/processes/1/operating-mode",
            data={"operating_mode": "startup"},
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.pop(require_operator, None)
        app.dependency_overrides.pop(require_developer, None)

    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard/processes/1"
    db.update_asset_instance.assert_awaited_once()
    update_call = db.update_asset_instance.await_args
    updates = update_call.args[1]
    assert updates["operating_mode"] == "startup"
    assert updates["operating_mode_changed_at"] is not None
    assert updates["operating_mode_changed_by_user_id"] == 42

    # Audit log: category='mode', action='changed'
    audit = db.insert_audit_log.await_args.args[0]
    assert isinstance(audit, AuditLogEntry)
    assert audit.category == "mode"
    assert audit.action == "changed"
    assert audit.entity_type == "asset_instance"


def test_mode_toggle_invalid_mode_returns_400(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gecersiz mode degeri 400 doner."""
    _setup_db_for_toggle(monkeypatch)
    try:
        response = client.post(
            "/dashboard/processes/1/operating-mode",
            data={"operating_mode": "INVALID"},
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.pop(require_operator, None)
        app.dependency_overrides.pop(require_developer, None)
    assert response.status_code == 400


def test_mode_toggle_same_mode_skips_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ayni mode -> update atlanir, redirect yapilir (DB yormak icin)."""
    db = _setup_db_for_toggle(monkeypatch)
    # get_asset_instance running doner — ayni mode'a toggle istegi
    try:
        response = client.post(
            "/dashboard/processes/1/operating-mode",
            data={"operating_mode": "running"},
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.pop(require_operator, None)
        app.dependency_overrides.pop(require_developer, None)
    assert response.status_code == 303
    db.update_asset_instance.assert_not_awaited()
    db.insert_audit_log.assert_not_awaited()


def test_mode_toggle_missing_instance_returns_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bulunamayan instance icin 404."""
    db = _setup_db_for_toggle(monkeypatch)
    db.get_asset_instance = AsyncMock(return_value=None)
    try:
        response = client.post(
            "/dashboard/processes/9999/operating-mode",
            data={"operating_mode": "startup"},
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.pop(require_operator, None)
        app.dependency_overrides.pop(require_developer, None)
    assert response.status_code == 404


# --- _compute_mode_spc_summary helper ----------------------------------------


@pytest.mark.asyncio
async def test_mode_spc_summary_empty_state() -> None:
    """Hicbir SPC state, hicbir audit log, default running mode'lar."""
    db = MagicMock()
    db.list_audit_log = AsyncMock(return_value=[])
    db.list_tags = AsyncMock(return_value=[])
    db.list_spc_states = AsyncMock(return_value=[])
    db.list_alarm_events = AsyncMock(return_value=[])

    instances = [_instance(1), _instance(2)]
    since = datetime.now(UTC) - timedelta(hours=24)
    summary = await _compute_mode_spc_summary(db, instances, since=since)

    assert summary["running_count"] == 2
    assert summary["startup_count"] == 0
    assert summary["shutdown_count"] == 0
    assert summary["idle_count"] == 0
    assert summary["suppressed_24h"] == 0
    assert summary["enabled_count"] == 0
    assert summary["learned_count"] == 0
    assert summary["alarms_24h"] == 0


@pytest.mark.asyncio
async def test_mode_spc_summary_mixed_modes_and_spc() -> None:
    """Karisik mode'lar + 1 ogrenme tamam SPC + 24h alarm."""
    db = MagicMock()
    now = datetime.now(UTC)
    # Mode change audit log — son 24h icinde 1 kayit
    db.list_audit_log = AsyncMock(
        return_value=[
            AuditLogEntry(
                category="mode",
                action="changed",
                entity_type="asset_instance",
                entity_id="1",
                detail="running -> startup",
                timestamp=now - timedelta(hours=2),
            ),
        ],
    )
    # Aktif tag — 2 spc_enabled, 1 spc_disabled
    db.list_tags = AsyncMock(
        return_value=[
            TagRecord(
                tag_id="T1", name="t1", modbus_host="h",
                register_address=0, spc_enabled=True,
            ),
            TagRecord(
                tag_id="T2", name="t2", modbus_host="h",
                register_address=1, spc_enabled=True,
            ),
            TagRecord(
                tag_id="T3", name="t3", modbus_host="h",
                register_address=2, spc_enabled=False,
            ),
        ],
    )
    # Bir tag ogrenme tamamlamis, digeri ogreniyor
    db.list_spc_states = AsyncMock(
        return_value=[
            SpcState(
                tag_id="T1", sample_count=100, learning_complete=True,
                mad_median=5.0, mad_value=0.5,
            ),
            SpcState(tag_id="T2", sample_count=30, learning_complete=False),
        ],
    )

    # 1 alarm son 24h icinde, 1 alarm pencerenin disinda
    async def _list_alarm_events(
        *,
        state: str | None = None,
        source: str | None = None,
        limit: int = 100,
    ) -> list[AlarmEvent]:
        if source != "spc":
            return []
        if state == "triggered":
            return [
                AlarmEvent(
                    tag_id="T1",
                    state="triggered",
                    triggered_at=now - timedelta(hours=1),
                    source="spc",
                    severity="warn",
                ),
            ]
        return []

    db.list_alarm_events = AsyncMock(side_effect=_list_alarm_events)

    instances = [
        _instance(1, operating_mode="running"),
        _instance(2, operating_mode="startup"),
        _instance(3, operating_mode="idle"),
    ]
    since = now - timedelta(hours=24)
    summary = await _compute_mode_spc_summary(db, instances, since=since)

    assert summary["running_count"] == 1
    assert summary["startup_count"] == 1
    assert summary["idle_count"] == 1
    assert summary["shutdown_count"] == 0
    assert summary["suppressed_24h"] == 1
    assert summary["enabled_count"] == 2
    assert summary["learned_count"] == 1
    assert summary["alarms_24h"] == 1


def test_mode_toggle_operator_can_toggle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Operator + Developer mode toggle edebilir (saha ihtiyaci)."""
    db = _setup_db_for_toggle(monkeypatch)
    # Operator session zaten kuruldu — endpoint require_operator'u uyguluyor.
    try:
        response = client.post(
            "/dashboard/processes/1/operating-mode",
            data={"operating_mode": "shutdown"},
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.pop(require_operator, None)
        app.dependency_overrides.pop(require_developer, None)
    assert response.status_code == 303
    db.update_asset_instance.assert_awaited_once()
