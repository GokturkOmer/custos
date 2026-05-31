"""R-06 / V11-306: ``EscalationLoop._tick`` davranış birimi.

Strateji: DB için ihtiyaç duyulan metodları stub'layan minimal sınıf.
``send_push_notifications`` modül-level fonksiyon olduğu için ``monkeypatch``
ile yerine mock konur.

Sınanan davranışlar (review M5/M6/H6b sonrası):

1. Eşik altındaki alarm yükseltilmez (threshold-1 dk yaş).
2. Eşik üstündeki warn alarm crit'e yükseltilir + audit log + push.
3. Zaten yükseltilmiş alarm tekrar yükseltilmez (``escalated_from`` dolu).
4. ``is_test=True`` alarm yükseltilmez (bakım modu).
5. Severity ``warn`` dışı (ör. ``info``, ``crit``) alarm yükseltilmez.
6. **Acknowledge edilmiş warn alarm YÜKSELTİLMEZ** — acknowledge artık
   escalation'ı durduran işarettir (review H6b; eski davranış tersiydi).
7. Otomatik kaynaklı (liveness/anomaly/spc/watchdog/rate_of_change) warn
   alarm yükseltilmez — kullanıcı kuralı: crit sadece kullanıcı tanımlı
   kaynaklarda (threshold/cross_sensor).
8. ``cross_sensor`` kaynaklı warn alarm crit'e yükseltilir.
9. **M5 yarış koruması:** aday seçildikten sonra alarm kapanırsa atomik
   ``escalate_alarm_to_crit`` ``None`` döner → audit/push yazılmaz (sahte crit
   gönderilmez).

Filtreleme artık DB tarafında (``list_escalatable_alarms``, review M6/H6b);
``_StubDB`` bu sözleşmeyi birebir taklit eder, böylece 1/3/4/5/6/7 davranışları
stub filtresinde doğrulanır. Gerçek SQL filtre/sıralaması integration testinde
sınanır.
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

    ``alarms`` tüm alarm'lardır; ``list_escalatable_alarms`` bunları gerçek SQL
    sözleşmesiyle aynı şekilde filtreler (triggered + warn + escalated_from None
    + not is_test + source in sources + triggered_at <= cutoff), en eski önce.
    ``escalate_returns_none=True`` ise ``escalate_alarm_to_crit`` ``None`` döner
    — eşzamanlı clear yarışı (review M5).

    Yapılan ``escalate_alarm_to_crit`` ve audit log çağrıları test sonunda
    iddia edilebilir.
    """

    def __init__(
        self,
        *,
        alarms: list[AlarmEvent] | None = None,
        escalation_minutes: int = 30,
        escalate_returns_none: bool = False,
    ) -> None:
        self._alarms = list(alarms or [])
        self._escalate_returns_none = escalate_returns_none
        self.escalate_calls: list[tuple[int, str, datetime]] = []
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

    async def list_escalatable_alarms(
        self,
        *,
        sources: list[str],
        triggered_before: datetime,
        limit: int = 200,
    ) -> list[AlarmEvent]:
        candidates = [
            a
            for a in self._alarms
            if a.state == "triggered"
            and a.severity == "warn"
            and a.escalated_from is None
            and not a.is_test
            and a.source in sources
            and a.triggered_at is not None
            and a.triggered_at <= triggered_before
        ]
        # En eski önce (ASC) — production sıralamasıyla aynı (review M6).
        candidates.sort(
            key=lambda a: a.triggered_at or datetime.min.replace(tzinfo=UTC),
        )
        return candidates[:limit]

    async def escalate_alarm_to_crit(
        self,
        event_id: int,
        *,
        old_severity: str,
        escalated_at: datetime,
    ) -> AlarmEvent | None:
        self.escalate_calls.append((event_id, old_severity, escalated_at))
        if self._escalate_returns_none:
            # Atomik UPDATE 0 satır etkiledi (yarışta kapanmış) — review M5.
            return None
        return AlarmEvent(
            id=event_id,
            tag_id="TEST",
            state="triggered",
            severity="crit",
            escalated_from=old_severity,
            escalated_at=escalated_at,
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
    state: str = "triggered",
) -> AlarmEvent:
    """Test alarm'ı üretir; ``age_minutes`` triggered_at'i geçmişe iter.

    Default ``source='threshold'`` + ``state='triggered'`` — kullanıcı tanımlı,
    escalate edilebilir aday. Diğer durumlar testlerde override edilir.
    """
    triggered = datetime.now(UTC) - timedelta(minutes=age_minutes)
    return AlarmEvent(
        id=alarm_id,
        tag_id=f"TAG_{alarm_id}",
        threshold_id=None,
        state=state,
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
        alarms=[_make_alarm(alarm_id=1, age_minutes=29.0)],
        escalation_minutes=30,
    )
    loop = EscalationLoop(db=db)  # type: ignore[arg-type]
    await loop._tick()
    assert db.escalate_calls == []
    assert _stub_push == []


async def test_warn_alarm_above_threshold_is_escalated(
    _stub_push: list[dict[str, Any]],
) -> None:
    """31 dk yaşındaki warn alarm, 30 dk eşik üstü → crit'e yükseltilir."""
    db = _StubDB(
        alarms=[_make_alarm(alarm_id=42, age_minutes=31.0)],
        escalation_minutes=30,
    )
    loop = EscalationLoop(db=db)  # type: ignore[arg-type]
    await loop._tick()
    assert len(db.escalate_calls) == 1
    event_id, old_severity, _ = db.escalate_calls[0]
    assert event_id == 42
    assert old_severity == "warn"
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
    """``escalated_from`` doluysa aday değil — tekrar yükseltme yok."""
    db = _StubDB(
        alarms=[
            _make_alarm(alarm_id=7, age_minutes=120.0, escalated_from="warn"),
        ],
        escalation_minutes=30,
    )
    loop = EscalationLoop(db=db)  # type: ignore[arg-type]
    await loop._tick()
    assert db.escalate_calls == []


async def test_test_alarm_is_not_escalated(
    _stub_push: list[dict[str, Any]],
) -> None:
    """is_test=True bakım modu alarm'ı eşik aşsa da yükseltilmez."""
    db = _StubDB(
        alarms=[_make_alarm(alarm_id=9, age_minutes=120.0, is_test=True)],
        escalation_minutes=30,
    )
    loop = EscalationLoop(db=db)  # type: ignore[arg-type]
    await loop._tick()
    assert db.escalate_calls == []


async def test_non_warn_severity_is_not_escalated(
    _stub_push: list[dict[str, Any]],
) -> None:
    """info / crit severity alarm yükseltme döngüsünün konusu değil."""
    db = _StubDB(
        alarms=[
            _make_alarm(alarm_id=11, age_minutes=120.0, severity="info"),
            _make_alarm(alarm_id=12, age_minutes=120.0, severity="crit"),
        ],
        escalation_minutes=30,
    )
    loop = EscalationLoop(db=db)  # type: ignore[arg-type]
    await loop._tick()
    assert db.escalate_calls == []


async def test_acknowledged_warn_alarm_is_not_escalated(
    _stub_push: list[dict[str, Any]],
) -> None:
    """H6b: acknowledge escalation'ı durdurur — onaylanmış warn yükseltilmez.

    Eski davranış tersiydi (acknowledged alarm da yükseltiliyordu); review H6
    ile acknowledge artık operatörün sahiplendiğini gösteren, zorunlu crit'i
    durduran işaret.
    """
    db = _StubDB(
        alarms=[
            _make_alarm(alarm_id=99, age_minutes=45.0, state="acknowledged"),
        ],
        escalation_minutes=30,
    )
    loop = EscalationLoop(db=db)  # type: ignore[arg-type]
    await loop._tick()
    assert db.escalate_calls == []
    assert _stub_push == []


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
        alarms=[
            _make_alarm(alarm_id=50, age_minutes=120.0, source=auto_source),
        ],
        escalation_minutes=30,
    )
    loop = EscalationLoop(db=db)  # type: ignore[arg-type]
    await loop._tick()
    assert db.escalate_calls == []
    assert db.audit_calls == []
    assert _stub_push == []


async def test_cross_sensor_alarm_is_escalated(
    _stub_push: list[dict[str, Any]],
) -> None:
    """Cross-sensor alarmları kullanıcı tanımlı kuralla üretilir → crit'e
    yükseltilir."""
    db = _StubDB(
        alarms=[
            _make_alarm(alarm_id=77, age_minutes=45.0, source="cross_sensor"),
        ],
        escalation_minutes=30,
    )
    loop = EscalationLoop(db=db)  # type: ignore[arg-type]
    await loop._tick()
    assert len(db.escalate_calls) == 1
    event_id, old_severity, _ = db.escalate_calls[0]
    assert event_id == 77
    assert old_severity == "warn"


async def test_escalation_skips_audit_push_when_alarm_cleared_in_race(
    _stub_push: list[dict[str, Any]],
) -> None:
    """M5: aday seçildikten sonra alarm kapanırsa atomik UPDATE 0 satır döner
    (``escalate_alarm_to_crit`` → None) → audit/push yazılmaz, sahte crit yok.
    """
    db = _StubDB(
        alarms=[_make_alarm(alarm_id=55, age_minutes=45.0)],
        escalation_minutes=30,
        escalate_returns_none=True,
    )
    loop = EscalationLoop(db=db)  # type: ignore[arg-type]
    await loop._tick()
    # Yükseltme denendi (atomik çağrı yapıldı) ama UPDATE boş döndü.
    assert len(db.escalate_calls) == 1
    # Sahte crit yan etkisi yok.
    assert db.audit_calls == []
    assert _stub_push == []
