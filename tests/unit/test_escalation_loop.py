"""R-06 / V11-306: ``EscalationLoop._tick`` davranış birimi.

Strateji: DB için ihtiyaç duyulan metodları stub'layan minimal sınıf
(MagicMock yerine custom — async metodlar için spec uyumlu daha temiz).
``send_push_notifications`` modül-level fonksiyon olduğu için
``monkeypatch`` ile yerine mock konur.

Sınanan davranışlar:

1. Eşik altındaki alarm yükseltilmez (threshold-1 dk yaş).
2. Eşik üstündeki warn alarm crit'e yükseltilir + audit log + push.
3. Zaten yükseltilmiş alarm tekrar dokunulmaz.
4. ``is_test=True`` alarm yükseltme yapılmaz (bakım modu).
5. Severity ``warn`` dışı (ör. ``info``, ``crit``) alarm yükseltilmez.
6. Otomatik kaynaklı (liveness/anomaly/spc/watchdog/rate_of_change)
   warn alarm yükseltilmez — kullanıcı kuralı: crit sadece kullanıcı
   tanımlı kaynaklarda (threshold/cross_sensor).
7. ``cross_sensor`` kaynaklı warn alarm crit'e yükseltilir.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from custos.analytics.escalation import EscalationLoop
from custos.shared.database import (
    AlarmEvent,
    AuditLogEntry,
    RetentionConfig,
)


class _StubDB:
    """``EscalationLoop`` için yeterli minimum DB yüzeyi.

    ``triggered`` listesi state='triggered' alarm'ları, ``acknowledged``
    listesi de aynı şekilde döner. ``escalation_warn_to_crit_minutes``
    constructor'da geçilir.

    Kayıt edilen update'ler ve audit log'lar test sonunda iddia edilebilir.
    """

    def __init__(
        self,
        *,
        triggered: list[AlarmEvent] | None = None,
        acknowledged: list[AlarmEvent] | None = None,
        escalation_minutes: int = 30,
    ) -> None:
        self._triggered = list(triggered or [])
        self._acknowledged = list(acknowledged or [])
        self._escalation_minutes = escalation_minutes
        self.update_calls: list[tuple[int, dict[str, Any]]] = []
        self.audit_calls: list[AuditLogEntry] = []
        self.cfg = RetentionConfig(
            raw_retention_days=180,
            auto_clean_enabled=True,
            updated_at=datetime.now(UTC),
            updated_by="test",
            escalation_warn_to_crit_minutes=escalation_minutes,
        )

    async def get_retention_config(self) -> RetentionConfig:
        return self.cfg

    async def list_alarm_events(
        self,
        *,
        state: str | None = None,
        tag_id: str | None = None,
        limit: int = 100,
        is_test: bool | None = None,
        source: str | None = None,
    ) -> list[AlarmEvent]:
        if state == "triggered":
            return list(self._triggered)
        if state == "acknowledged":
            return list(self._acknowledged)
        return []

    async def update_alarm_event(
        self,
        event_id: int,
        updates: dict[str, Any],
    ) -> AlarmEvent | None:
        self.update_calls.append((event_id, dict(updates)))
        # Geri dönüş için sahte alarm: çağrı doğruluğu yeterli.
        return AlarmEvent(
            id=event_id,
            tag_id="TEST",
            state="acknowledged",
            severity="crit",
            escalated_from="warn",
            escalated_at=updates.get("escalated_at"),
        )

    async def insert_audit_log(self, entry: AuditLogEntry) -> AuditLogEntry:
        self.audit_calls.append(entry)
        return entry


def _make_alarm(
    *,
    alarm_id: int,
    severity: str = "warn",
    age_minutes: float = 0.0,
    is_test: bool = False,
    escalated_from: str | None = None,
    source: str = "threshold",
) -> AlarmEvent:
    """Test alarm'ı üretir; ``age_minutes`` triggered_at'i geçmişe iter.

    Default ``source='threshold'`` — kullanıcı tanımlı kaynak, escalate
    edilebilir. Otomatik kaynak testleri için açıkça override edilir.
    """
    triggered = datetime.now(UTC) - timedelta(minutes=age_minutes)
    return AlarmEvent(
        id=alarm_id,
        tag_id=f"TAG_{alarm_id}",
        threshold_id=None,
        state="triggered",
        triggered_at=triggered,
        severity=severity,
        is_test=is_test,
        escalated_from=escalated_from,
        source=source,
    )


@pytest.fixture
def _stub_push(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """``send_push_notifications``'ı modül seviyesinde stub'lar.

    Çağrı argümanlarını listeye toplayıp test'e döner.
    """
    calls: list[dict[str, Any]] = []

    async def _fake_push(**kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(
        "custos.analytics.escalation.send_push_notifications",
        _fake_push,
    )
    return calls


async def test_alarm_below_threshold_is_not_escalated(
    _stub_push: list[dict[str, Any]],
) -> None:
    """29 dk yaşındaki warn alarm, 30 dk eşik altı → yükseltme yok."""
    db = _StubDB(
        triggered=[_make_alarm(alarm_id=1, age_minutes=29.0)],
        escalation_minutes=30,
    )
    loop = EscalationLoop(db=db)  # type: ignore[arg-type]
    await loop._tick()
    assert db.update_calls == []
    assert _stub_push == []


async def test_warn_alarm_above_threshold_is_escalated(
    _stub_push: list[dict[str, Any]],
) -> None:
    """31 dk yaşındaki warn alarm, 30 dk eşik üstü → crit'e yükseltilir."""
    db = _StubDB(
        triggered=[_make_alarm(alarm_id=42, age_minutes=31.0)],
        escalation_minutes=30,
    )
    loop = EscalationLoop(db=db)  # type: ignore[arg-type]
    await loop._tick()
    assert len(db.update_calls) == 1
    event_id, updates = db.update_calls[0]
    assert event_id == 42
    assert updates["severity"] == "crit"
    assert updates["escalated_from"] == "warn"
    assert updates["escalated_at"] is not None
    # Audit log + push çağrıldı.
    assert len(db.audit_calls) == 1
    audit = db.audit_calls[0]
    assert audit.category == "alarm_escalation"
    assert audit.action == "warn_to_crit"
    assert len(_stub_push) == 1
    assert _stub_push[0]["severity"] == "crit"
    assert _stub_push[0]["is_test"] is False


async def test_already_escalated_alarm_is_skipped(
    _stub_push: list[dict[str, Any]],
) -> None:
    """``escalated_from`` doluysa tick atlar — tekrar yükseltme yok."""
    db = _StubDB(
        triggered=[
            _make_alarm(
                alarm_id=7,
                age_minutes=120.0,
                escalated_from="warn",
            ),
        ],
        escalation_minutes=30,
    )
    loop = EscalationLoop(db=db)  # type: ignore[arg-type]
    await loop._tick()
    assert db.update_calls == []


async def test_test_alarm_is_not_escalated(
    _stub_push: list[dict[str, Any]],
) -> None:
    """is_test=True bakım modu alarm'ı eşik aşsa da yükseltilmez."""
    db = _StubDB(
        triggered=[_make_alarm(alarm_id=9, age_minutes=120.0, is_test=True)],
        escalation_minutes=30,
    )
    loop = EscalationLoop(db=db)  # type: ignore[arg-type]
    await loop._tick()
    assert db.update_calls == []


async def test_non_warn_severity_is_not_escalated(
    _stub_push: list[dict[str, Any]],
) -> None:
    """info / crit severity alarm yükseltme döngüsünün konusu değil."""
    db = _StubDB(
        triggered=[
            _make_alarm(alarm_id=11, age_minutes=120.0, severity="info"),
            _make_alarm(alarm_id=12, age_minutes=120.0, severity="crit"),
        ],
        escalation_minutes=30,
    )
    loop = EscalationLoop(db=db)  # type: ignore[arg-type]
    await loop._tick()
    assert db.update_calls == []


async def test_acknowledged_warn_alarm_is_escalated(
    _stub_push: list[dict[str, Any]],
) -> None:
    """Operatör onaylasa bile alarm hâlâ açıksa yükseltme devam eder."""
    db = _StubDB(
        acknowledged=[_make_alarm(alarm_id=99, age_minutes=45.0)],
        escalation_minutes=30,
    )
    loop = EscalationLoop(db=db)  # type: ignore[arg-type]
    await loop._tick()
    assert len(db.update_calls) == 1
    event_id, updates = db.update_calls[0]
    assert event_id == 99
    assert updates["severity"] == "crit"


@pytest.mark.parametrize(
    "auto_source",
    ["liveness", "anomaly", "spc", "watchdog", "rate_of_change"],
)
async def test_auto_source_alarm_is_not_escalated(
    auto_source: str,
    _stub_push: list[dict[str, Any]],
) -> None:
    """Otomatik kaynaklar (liveness/anomaly/spc/watchdog/rate_of_change)
    eşik aşsa bile crit'e yükseltilmez. Kullanıcı kuralı: critical sadece
    kullanıcı tanımlı kaynaklarda (threshold/cross_sensor)."""
    db = _StubDB(
        triggered=[
            _make_alarm(alarm_id=50, age_minutes=120.0, source=auto_source),
        ],
        escalation_minutes=30,
    )
    loop = EscalationLoop(db=db)  # type: ignore[arg-type]
    await loop._tick()
    assert db.update_calls == []
    assert db.audit_calls == []
    assert _stub_push == []


async def test_cross_sensor_alarm_is_escalated(
    _stub_push: list[dict[str, Any]],
) -> None:
    """Cross-sensor alarmları kullanıcı tanımlı kuralla üretilir → crit'e
    yükseltilir."""
    db = _StubDB(
        triggered=[
            _make_alarm(alarm_id=77, age_minutes=45.0, source="cross_sensor"),
        ],
        escalation_minutes=30,
    )
    loop = EscalationLoop(db=db)  # type: ignore[arg-type]
    await loop._tick()
    assert len(db.update_calls) == 1
    event_id, updates = db.update_calls[0]
    assert event_id == 77
    assert updates["severity"] == "crit"
    assert updates["escalated_from"] == "warn"
