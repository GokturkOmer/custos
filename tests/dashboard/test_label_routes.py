"""Etiketleme + Review Queue (R-05 / V11-301) testleri.

Kapsam:

- Unit: ``_compute_label_summary`` saf — mock DB ile birim test.
- Integration: ``POST /dashboard/alarms/{id}/label`` 4 sınıf için 200 + audit
  log + upsert çağrısı; geçersiz sınıf 400; bilinmeyen alarm 404.
- Dashboard: ``/dashboard/alarms?unlabeled=true`` filtresi etiketsiz alarm'ları
  döndürür; ``/dashboard/ml`` ``#section-labeling`` 4 sınıf sayımını render eder.

Mock DB ``conftest.py`` tarafından bypass edilen auth'tan bağımsız çalışır;
``app.state.db`` monkeypatch ile kurulur.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from custos.__main__ import app
from custos.analytics.dashboard.app import _compute_label_summary
from custos.shared.database import (
    LABEL_CLASS_VALUES,
    AlarmEvent,
    AlarmEventLabel,
    RetentionConfig,
)

client = TestClient(app)


# --- Yardımcılar -----------------------------------------------------------


def _mk_alarm(
    event_id: int,
    *,
    tag_id: str = "TEST_TAG",
    state: str = "triggered",
    triggered_at: datetime | None = None,
) -> AlarmEvent:
    return AlarmEvent(
        id=event_id,
        threshold_id=None,
        tag_id=tag_id,
        state=state,
        triggered_at=triggered_at or datetime.now(UTC),
        trigger_value=42.0,
        source="anomaly",
        severity="warn",
    )


def _mk_label(
    alarm_event_id: int,
    label_class: str = "gercek_ariza",
    *,
    user_id: int = 1,
) -> AlarmEventLabel:
    return AlarmEventLabel(
        id=alarm_event_id,
        alarm_event_id=alarm_event_id,
        label_class=label_class,
        labeled_by_user_id=user_id,
        notes="",
        labeled_at=datetime.now(UTC),
    )


def _retention_config() -> RetentionConfig:
    return RetentionConfig(
        raw_retention_days=365,
        auto_clean_enabled=True,
        updated_at=datetime.now(UTC),
        updated_by="test",
    )


class _LabelMockDB:
    """Etiketleme paketi için minimal mock DB.

    Dashboard route'larının dolaylı çağırdığı yardımcı metodları (threshold,
    asset, anomaly...) hep boş/None döndürür; sadece etiket ile ilgili
    metodlar canlı veriye sahiptir. Bu, test'leri R-05 davranışına odaklı tutar.
    """

    def __init__(
        self,
        *,
        alarms: list[AlarmEvent] | None = None,
        labels: dict[int, AlarmEventLabel] | None = None,
        unlabeled_alarms: list[AlarmEvent] | None = None,
    ) -> None:
        self._alarms: dict[int, AlarmEvent] = {a.id: a for a in (alarms or []) if a.id is not None}
        self._labels: dict[int, AlarmEventLabel] = labels or {}
        self._unlabeled_alarms: list[AlarmEvent] = unlabeled_alarms or []
        self.upsert_calls: list[tuple[int, str, int, str]] = []
        self.audit_log_calls: list[Any] = []
        self.list_unlabeled_calls: list[int] = []
        self.count_labels_calls: list[datetime | None] = []

    # --- Etiket metotları ---

    async def upsert_alarm_label(
        self,
        alarm_event_id: int,
        label_class: str,
        labeled_by_user_id: int,
        notes: str = "",
    ) -> AlarmEventLabel:
        self.upsert_calls.append((alarm_event_id, label_class, labeled_by_user_id, notes))
        label = AlarmEventLabel(
            id=len(self.upsert_calls),
            alarm_event_id=alarm_event_id,
            label_class=label_class,
            labeled_by_user_id=labeled_by_user_id,
            notes=notes,
            labeled_at=datetime.now(UTC),
        )
        self._labels[alarm_event_id] = label
        return label

    async def get_alarm_label(
        self, alarm_event_id: int,
    ) -> AlarmEventLabel | None:
        return self._labels.get(alarm_event_id)

    async def list_unlabeled_alarms(
        self, limit: int = 100,
    ) -> list[AlarmEvent]:
        self.list_unlabeled_calls.append(limit)
        return self._unlabeled_alarms[:limit]

    async def count_labels_by_class(
        self, since: datetime | None = None,
    ) -> dict[str, int]:
        self.count_labels_calls.append(since)
        counts: dict[str, int] = dict.fromkeys(LABEL_CLASS_VALUES, 0)
        for lbl in self._labels.values():
            counts[lbl.label_class] = counts.get(lbl.label_class, 0) + 1
        return counts

    # --- Alarm event ---

    async def get_alarm_event(self, event_id: int) -> AlarmEvent | None:
        return self._alarms.get(event_id)

    async def list_alarm_events(
        self,
        state: str | None = None,
        tag_id: str | None = None,
        limit: int = 100,
        is_test: bool | None = None,
        source: str | None = None,
    ) -> list[AlarmEvent]:
        # Aktif alarm sayfası testi için: triggered/acknowledged döndür.
        results = [
            a for a in self._alarms.values() if state is None or a.state == state
        ]
        return results[:limit]

    async def count_alarm_events_for_threshold(
        self, threshold_id: int, since: datetime | None = None,
    ) -> int:
        return 0

    # --- Audit log ---

    async def insert_audit_log(self, entry: Any) -> Any:
        self.audit_log_calls.append(entry)
        return entry

    # --- Dolaylı kullanılan no-op'lar ---

    async def get_threshold(self, threshold_id: int) -> Any:
        return None

    async def get_alarm_checklist_mapping(self, threshold_id: int) -> Any:
        return None

    async def list_asset_instances(self) -> list[Any]:
        return []

    async def count_anomalies(self, since: datetime | None = None) -> int:
        return 0

    async def get_retention_config(self) -> RetentionConfig:
        return _retention_config()

    async def get_latest_anomaly_score(self, instance_id: int) -> Any:
        return None


@pytest.fixture
def label_db(monkeypatch: pytest.MonkeyPatch) -> _LabelMockDB:
    """Default mock DB — boş etiket havuzu, 1 etiketli alarm."""
    db = _LabelMockDB(
        alarms=[_mk_alarm(1, tag_id="TEST_T1")],
        labels={},
        unlabeled_alarms=[_mk_alarm(1, tag_id="TEST_T1")],
    )
    monkeypatch.setattr(app.state, "db", db, raising=False)
    return db


# --- Unit: _compute_label_summary -----------------------------------------


@pytest.mark.asyncio
async def test_compute_label_summary_empty() -> None:
    """Hiç etiket yok — counts 4 anahtarda 0, unlabeled_count = 0."""
    mock = MagicMock()
    mock.count_labels_by_class = AsyncMock(
        return_value=dict.fromkeys(LABEL_CLASS_VALUES, 0),
    )
    mock.list_unlabeled_alarms = AsyncMock(return_value=[])

    summary = await _compute_label_summary(mock)

    assert set(summary["counts"].keys()) == LABEL_CLASS_VALUES
    assert all(v == 0 for v in summary["counts"].values())
    assert summary["unlabeled_count"] == 0
    assert summary["unlabeled_truncated"] is False
    # since=None default
    mock.count_labels_by_class.assert_awaited_once_with(since=None)


@pytest.mark.asyncio
async def test_compute_label_summary_with_since() -> None:
    """since geçilirse count_labels_by_class kwarg ile aktarılır."""
    mock = MagicMock()
    mock.count_labels_by_class = AsyncMock(
        return_value={
            "gercek_ariza": 3,
            "yanlis_alarm": 1,
            "bakim_sirasinda": 0,
            "bilinmiyor": 2,
        },
    )
    mock.list_unlabeled_alarms = AsyncMock(
        return_value=[_mk_alarm(i) for i in range(5)],
    )

    since = datetime.now(UTC) - timedelta(days=30)
    summary = await _compute_label_summary(mock, since=since)

    assert summary["counts"]["gercek_ariza"] == 3
    assert summary["counts"]["bakim_sirasinda"] == 0
    assert summary["unlabeled_count"] == 5
    mock.count_labels_by_class.assert_awaited_once_with(since=since)


@pytest.mark.asyncio
async def test_compute_label_summary_truncated() -> None:
    """Etiketsiz alarm sayısı tavanı aşarsa unlabeled_truncated=True."""
    probe_limit = 3
    mock = MagicMock()
    mock.count_labels_by_class = AsyncMock(
        return_value=dict.fromkeys(LABEL_CLASS_VALUES, 0),
    )
    mock.list_unlabeled_alarms = AsyncMock(
        return_value=[_mk_alarm(i) for i in range(probe_limit)],
    )

    summary = await _compute_label_summary(
        mock, unlabeled_probe_limit=probe_limit,
    )

    assert summary["unlabeled_count"] == probe_limit
    assert summary["unlabeled_truncated"] is True


# --- Integration: POST /alarms/{id}/label -----------------------------------


@pytest.mark.parametrize("cls", sorted(LABEL_CLASS_VALUES))
def test_label_endpoint_each_class_returns_partial(
    label_db: _LabelMockDB, cls: str,
) -> None:
    """4 sınıf için sırayla 200 + upsert + audit log + partial."""
    response = client.post(f"/dashboard/alarms/1/label?cls={cls}")
    assert response.status_code == 200
    text = response.text
    assert 'id="alarm-row-1"' in text
    # Etiket buton'larından dördü de partial içinde olmalı (re-label imkanı için)
    for any_cls in LABEL_CLASS_VALUES:
        assert f"cls={any_cls}" in text

    # DB upsert tek seferde, doğru parametrelerle
    assert label_db.upsert_calls
    last = label_db.upsert_calls[-1]
    assert last[0] == 1  # alarm_event_id
    assert last[1] == cls
    assert last[2] == 1  # conftest fake_dev_session.user_id

    # Audit log: category=ml, action=alarm_labeled
    assert label_db.audit_log_calls
    last_audit = label_db.audit_log_calls[-1]
    assert last_audit.category == "ml"
    assert last_audit.action == "alarm_labeled"
    assert last_audit.entity_type == "alarm_event"
    assert last_audit.entity_id == "1"
    assert cls in last_audit.detail


def test_label_endpoint_invalid_class_returns_400(
    label_db: _LabelMockDB,
) -> None:
    """LABEL_CLASS_VALUES dışı cls → 400, DB'ye dokunulmaz."""
    response = client.post(
        "/dashboard/alarms/1/label?cls=tamamen_yanlis",
        follow_redirects=False,
    )
    assert response.status_code == 400
    assert label_db.upsert_calls == []
    assert label_db.audit_log_calls == []


def test_label_endpoint_unknown_alarm_returns_404(
    label_db: _LabelMockDB,
) -> None:
    """Var olmayan alarm_id → 404, upsert yok."""
    response = client.post(
        "/dashboard/alarms/9999/label?cls=gercek_ariza",
        follow_redirects=False,
    )
    assert response.status_code == 404
    assert label_db.upsert_calls == []


def test_label_endpoint_repeat_call_updates_label(
    label_db: _LabelMockDB,
) -> None:
    """Aynı alarm tekrar etiketlenince upsert iki kez çağrılır (re-label)."""
    r1 = client.post("/dashboard/alarms/1/label?cls=gercek_ariza")
    assert r1.status_code == 200
    r2 = client.post("/dashboard/alarms/1/label?cls=yanlis_alarm")
    assert r2.status_code == 200

    assert len(label_db.upsert_calls) == 2
    assert label_db.upsert_calls[0][1] == "gercek_ariza"
    assert label_db.upsert_calls[1][1] == "yanlis_alarm"
    # Audit log re-label de düşer
    actions = [a.action for a in label_db.audit_log_calls]
    assert actions == ["alarm_labeled", "alarm_labeled"]


# --- Dashboard: alarms.html unlabeled filter --------------------------------


def test_alarms_page_unlabeled_filter_hides_labeled_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``?unlabeled=true`` etiketli alarm'ları gizler, etiketsizleri tutar."""
    a1 = _mk_alarm(1, tag_id="TEST_LABELED", state="triggered")
    a2 = _mk_alarm(2, tag_id="TEST_UNLABELED", state="triggered")
    db = _LabelMockDB(
        alarms=[a1, a2],
        labels={1: _mk_label(1)},
    )
    monkeypatch.setattr(app.state, "db", db, raising=False)

    # Filtre kapalıyken iki alarm da görünür
    r_off = client.get("/dashboard/alarms")
    assert r_off.status_code == 200
    assert "TEST_LABELED" in r_off.text
    assert "TEST_UNLABELED" in r_off.text

    # Filtre açıkken sadece etiketsiz görünür
    r_on = client.get("/dashboard/alarms?unlabeled=true")
    assert r_on.status_code == 200
    assert "TEST_UNLABELED" in r_on.text
    assert "TEST_LABELED" not in r_on.text
    # Checkbox checked olarak render olmuş olmalı
    assert "checked" in r_on.text


# --- Dashboard: ml.html section-labeling ------------------------------------


def test_ml_hub_renders_label_section(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ML hub etiketleme bölümü 4 sınıf sayımı + review queue link gösterir."""
    db = _LabelMockDB(
        alarms=[],
        labels={
            1: _mk_label(1, "gercek_ariza"),
            2: _mk_label(2, "gercek_ariza"),
            3: _mk_label(3, "yanlis_alarm"),
        },
        unlabeled_alarms=[_mk_alarm(10), _mk_alarm(11)],
    )
    monkeypatch.setattr(app.state, "db", db, raising=False)

    response = client.get("/dashboard/ml")
    assert response.status_code == 200
    text = response.text
    assert 'id="section-labeling"' in text
    assert "Etiketleme & Review Queue" in text
    # 4 sınıf adı
    assert "Gerçek arıza" in text
    assert "Yanlış alarm" in text
    assert "Bakım sırasında" in text
    assert "Bilinmiyor" in text
    # Etiketsiz: 2 alarm
    assert "Etiketlenmemiş" in text
    # Review queue link
    assert "/dashboard/alarms?unlabeled=true" in text
    # ML hub 30 gün penceresiyle çağrı yapmış olmalı
    assert db.count_labels_calls
    assert db.count_labels_calls[0] is not None
