"""SPC engine birim testleri (R-07 / V11-308).

EWMA + CUSUM + MAD-score saf matematik testleri ve engine tick
davranisi (ogrenme penceresi, alarm yazimi, cooldown).

DB icin minimal stub sinifi (mock yerine) async yuzeyi temiz tutar.
``send_push_notifications`` modul-level fonksiyon — ``monkeypatch``
ile sahte bir async fonksiyona donusturulur (push gerek yok).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from custos.analytics.spc_engine import (
    SPCEngine,
    _check_alarm,
    _median,
    _update_cusum,
    _update_ewma,
)
from custos.shared.database import (
    AlarmEvent,
    AuditLogEntry,
    RetentionConfig,
    SpcState,
    TagBinding,
    TagReading,
    TagRecord,
)


def _tag(
    tag_id: str = "T1",
    *,
    spc_enabled: bool = True,
    name: str = "Test Tag",
) -> TagRecord:
    return TagRecord(
        tag_id=tag_id,
        name=name,
        modbus_host="127.0.0.1",
        register_address=0,
        spc_enabled=spc_enabled,
    )


class _StubDB:
    """SPCEngine icin minimal DB yuzeyi.

    Saklanan state'ler ``upsert_spc_state`` ile kayit altina alinir;
    test sonunda assert edilir. ``list_tags`` / ``get_latest_tag_readings``
    constructor'da geclendirilir.
    """

    def __init__(
        self,
        *,
        tags: list[TagRecord] | None = None,
        latest: dict[str, TagReading] | None = None,
        bindings: list[TagBinding] | None = None,
        global_maint: bool = False,
    ) -> None:
        self._tags = list(tags or [])
        self._latest = dict(latest or {})
        self._bindings = list(bindings or [])
        self._spc_states: dict[str, SpcState] = {}
        self.alarm_events: list[AlarmEvent] = []
        self.audit_logs: list[AuditLogEntry] = []
        # Global maintenance icin RetentionConfig (P-04 saygi).
        self._cfg = RetentionConfig(
            raw_retention_days=180,
            auto_clean_enabled=True,
            updated_at=datetime.now(UTC),
            updated_by="test",
        )
        if global_maint:
            self._cfg.global_maintenance_until = None
            self._cfg.global_maintenance_started_at = datetime.now(UTC)
            self._cfg.global_maintenance_reason = "test"

    async def list_tags(
        self, status: str | None = None,
    ) -> list[TagRecord]:
        return list(self._tags)

    async def get_latest_tag_readings(
        self, tag_ids: list[str],
    ) -> dict[str, TagReading]:
        return {tid: r for tid, r in self._latest.items() if tid in tag_ids}

    async def list_tag_bindings_all(self) -> list[TagBinding]:
        return list(self._bindings)

    async def get_retention_config(self) -> RetentionConfig:
        return self._cfg

    async def list_active_maintenance_instances(
        self, now: datetime,
    ) -> list[Any]:
        return []

    async def get_spc_state(self, tag_id: str) -> SpcState | None:
        s = self._spc_states.get(tag_id)
        if s is None:
            return None
        # Return a copy so engine modifications don't leak directly until upsert.
        return SpcState(
            tag_id=s.tag_id,
            sample_count=s.sample_count,
            ewma_value=s.ewma_value,
            ewma_variance=s.ewma_variance,
            cusum_pos=s.cusum_pos,
            cusum_neg=s.cusum_neg,
            mad_median=s.mad_median,
            mad_value=s.mad_value,
            last_sample_at=s.last_sample_at,
            learning_complete=s.learning_complete,
        )

    async def upsert_spc_state(self, state: SpcState) -> SpcState:
        self._spc_states[state.tag_id] = SpcState(
            tag_id=state.tag_id,
            sample_count=state.sample_count,
            ewma_value=state.ewma_value,
            ewma_variance=state.ewma_variance,
            cusum_pos=state.cusum_pos,
            cusum_neg=state.cusum_neg,
            mad_median=state.mad_median,
            mad_value=state.mad_value,
            last_sample_at=state.last_sample_at,
            learning_complete=state.learning_complete,
        )
        return state

    async def list_spc_states(self) -> list[SpcState]:
        return list(self._spc_states.values())

    async def insert_alarm_event(self, event: AlarmEvent) -> AlarmEvent:
        self.alarm_events.append(event)
        return event

    async def insert_audit_log(self, entry: AuditLogEntry) -> AuditLogEntry:
        self.audit_logs.append(entry)
        return entry


# --- Saf matematik testleri ----------------------------------------------------


def test_median_odd_count() -> None:
    """Tek sayida ornek — orta deger."""
    assert _median([1.0, 3.0, 2.0]) == 2.0
    assert _median([5.0]) == 5.0


def test_median_even_count() -> None:
    """Cift sayida ornek — iki orta degerin ortalamasi."""
    assert _median([1.0, 2.0, 3.0, 4.0]) == 2.5


def test_ewma_first_sample_initializes() -> None:
    """Ilk ornek ewma_value'ya kopyalanir, variance 0 olur."""
    state = SpcState(tag_id="T1")
    _update_ewma(state, 5.0)
    assert state.ewma_value == 5.0
    assert state.ewma_variance == 0.0


def test_ewma_converges_to_constant() -> None:
    """100 sabit deger sonrasinda ewma_value ~5.0, variance ~0.0."""
    state = SpcState(tag_id="T1")
    for _ in range(100):
        _update_ewma(state, 5.0)
    assert state.ewma_value is not None
    assert abs(state.ewma_value - 5.0) < 1e-6
    assert state.ewma_variance is not None
    assert state.ewma_variance < 1e-6


def test_cusum_no_op_without_baseline() -> None:
    """Ogrenme tamamlanmadiysa CUSUM dokunmaz (mad_median None)."""
    state = SpcState(tag_id="T1")
    _update_cusum(state, 10.0)
    assert state.cusum_pos == 0.0
    assert state.cusum_neg == 0.0


def test_cusum_accumulates_positive_drift() -> None:
    """10 ornek baseline'in ustunde -> cusum_pos buyur, esik asar."""
    state = SpcState(
        tag_id="T1",
        mad_median=0.0,
        mad_value=1.0,  # sigma = 1.4826
        learning_complete=True,
    )
    # 10 ornek 5 birim sapmali — yeterli zamanli sapma
    for _ in range(10):
        _update_cusum(state, 5.0)
    # cusum_pos buyumus olmali
    assert state.cusum_pos > 0.0
    # Negatif kanal etkilenmemis
    assert state.cusum_neg == 0.0


def test_check_alarm_silent_during_learning() -> None:
    """Learning tamamlanmamissa hicbir alarm dondurulmez."""
    state = SpcState(tag_id="T1", sample_count=50, learning_complete=False)
    # Cilgin sapma — yine None doner.
    assert _check_alarm(state, 1000.0) is None


def test_check_alarm_mad_score_triggers() -> None:
    """Donmus median + MAD ile asiri sapan deger MAD alarmi tetikler."""
    state = SpcState(
        tag_id="T1",
        sample_count=100,
        mad_median=10.0,
        mad_value=0.5,  # 1.4826 * 0.5 = 0.7413 sigma
        learning_complete=True,
    )
    # 10 + 5 = 15 ; |15 - 10| / 0.7413 = ~6.74 z-score (3.5'in ustunde)
    result = _check_alarm(state, 15.0)
    assert result is not None
    assert result[0] == "mad"
    assert "MAD-score" in result[1]


def test_check_alarm_normal_value_no_alarm() -> None:
    """Baseline yakini deger hicbir alarm tetiklemez."""
    state = SpcState(
        tag_id="T1",
        sample_count=100,
        mad_median=10.0,
        mad_value=1.0,
        # EWMA da yakin baseline
        ewma_value=10.0,
        ewma_variance=1.0,
        learning_complete=True,
    )
    assert _check_alarm(state, 10.1) is None


# --- Engine tick davranisi -----------------------------------------------------


@pytest.mark.asyncio
async def test_engine_skips_when_no_spc_enabled_tags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """spc_enabled=False tag'ler tick'te islenmez (alarm olusmamali)."""
    db = _StubDB(
        tags=[_tag("T1", spc_enabled=False)],
        latest={
            "T1": TagReading(
                timestamp=datetime.now(UTC), tag_id="T1", value=5.0,
            ),
        },
    )
    engine = SPCEngine(db=db)  # type: ignore[arg-type]
    await engine._tick()
    assert db.alarm_events == []
    # spc_state kaydi da yok (tag isenmedi).
    states = await db.list_spc_states()
    assert states == []


@pytest.mark.asyncio
async def test_engine_learning_phase_silent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ilk 100 ornek alarm yazmaz; sample_count buyur, learning_complete False."""
    # send_push_notifications mock'u — push'u kullanmamamiz lazim (alarm yok).
    monkeypatch.setattr(
        "custos.analytics.spc_engine.send_push_notifications",
        _noop_push,
    )
    tag_id = "T1"
    db = _StubDB(tags=[_tag(tag_id)])
    engine = SPCEngine(db=db)  # type: ignore[arg-type]

    # 50 ornek (50 farkli timestamp) — her biri ayni deger.
    base = datetime.now(UTC)
    for i in range(50):
        ts = base + timedelta(seconds=i)
        db._latest[tag_id] = TagReading(
            timestamp=ts, tag_id=tag_id, value=10.0,
        )
        await engine._tick()

    state = await db.get_spc_state(tag_id)
    assert state is not None
    assert state.sample_count == 50
    assert state.learning_complete is False
    assert db.alarm_events == []


@pytest.mark.asyncio
async def test_engine_learning_completes_at_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """100. ornekte median + MAD donar, learning_complete=True olur."""
    monkeypatch.setattr(
        "custos.analytics.spc_engine.send_push_notifications",
        _noop_push,
    )
    tag_id = "T1"
    db = _StubDB(tags=[_tag(tag_id)])
    engine = SPCEngine(db=db)  # type: ignore[arg-type]

    base = datetime.now(UTC)
    for i in range(100):
        ts = base + timedelta(seconds=i)
        db._latest[tag_id] = TagReading(
            timestamp=ts, tag_id=tag_id, value=10.0,
        )
        await engine._tick()

    state = await db.get_spc_state(tag_id)
    assert state is not None
    assert state.sample_count == 100
    assert state.learning_complete is True
    assert state.mad_median == 10.0
    # Buffer hafizadan silinmis olmali (engine icinde private; sample_count'tan
    # dolayli yoluyla teyit ediyoruz).


@pytest.mark.asyncio
async def test_engine_post_learning_outlier_writes_alarm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ogrenme sonrasi outlier deger source='spc' alarm yazar."""
    monkeypatch.setattr(
        "custos.analytics.spc_engine.send_push_notifications",
        _noop_push,
    )
    tag_id = "T1"
    db = _StubDB(tags=[_tag(tag_id)])
    engine = SPCEngine(db=db)  # type: ignore[arg-type]

    base = datetime.now(UTC)
    # 100 sabit ornek -> ogrenme tamam.
    for i in range(100):
        ts = base + timedelta(seconds=i)
        db._latest[tag_id] = TagReading(
            timestamp=ts, tag_id=tag_id, value=10.0,
        )
        await engine._tick()

    # Buyuk sapma (median 10, mad ~0 — epsilon ile clamped — z-score cok yuksek).
    db._latest[tag_id] = TagReading(
        timestamp=base + timedelta(seconds=200), tag_id=tag_id, value=50.0,
    )
    await engine._tick()

    # En az bir alarm yazilmali, source='spc'
    spc_alarms = [a for a in db.alarm_events if a.source == "spc"]
    assert len(spc_alarms) >= 1
    assert spc_alarms[0].severity == "warn"
    assert spc_alarms[0].tag_id == tag_id


@pytest.mark.asyncio
async def test_engine_cooldown_prevents_repeat_alarm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cooldown icindeyken ayni outlier ikinci alarmi tetiklemez."""
    monkeypatch.setattr(
        "custos.analytics.spc_engine.send_push_notifications",
        _noop_push,
    )
    tag_id = "T1"
    db = _StubDB(tags=[_tag(tag_id)])
    engine = SPCEngine(db=db)  # type: ignore[arg-type]

    base = datetime.now(UTC)
    for i in range(100):
        ts = base + timedelta(seconds=i)
        db._latest[tag_id] = TagReading(
            timestamp=ts, tag_id=tag_id, value=10.0,
        )
        await engine._tick()

    # Iki ardisik outlier — cooldown engellemeli.
    for j in range(2):
        db._latest[tag_id] = TagReading(
            timestamp=base + timedelta(seconds=200 + j * 10),
            tag_id=tag_id,
            value=50.0,
        )
        await engine._tick()

    spc_alarms = [a for a in db.alarm_events if a.source == "spc"]
    # Cooldown 30 dk — sadece bir alarm olmali (ikincisi cooldown'da bastirildi).
    assert len(spc_alarms) == 1


async def _noop_push(**kwargs: Any) -> None:
    """send_push_notifications icin sahte; hicbir sey yapma."""
