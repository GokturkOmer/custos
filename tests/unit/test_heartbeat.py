"""Cross-service watchdog heartbeat unit testleri (V11-105/K13).

DB'ye gerçekten bağlanmadan ``check_heartbeats`` ve ``overall_state``
mantığını doğrular. DB CRUD'u ayrı bir entegrasyon testi kapsar.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from custos.analytics.heartbeat import (
    CRIT_THRESHOLD_SECONDS,
    WARN_THRESHOLD_SECONDS,
    check_heartbeats,
    overall_state,
)
from custos.shared.database import ServiceHeartbeat


@dataclass
class _StubDB:
    """Sadece list_service_heartbeats sağlayan stub."""

    rows: list[ServiceHeartbeat] = field(default_factory=list)

    async def list_service_heartbeats(self) -> list[ServiceHeartbeat]:
        return list(self.rows)

    # check_heartbeats yalnızca bu metodu kullanır; diğerleri NotImplemented.
    def __getattr__(self, name: str) -> Any:  # noqa: D401
        raise AttributeError(name)


def _hb(name: str, age_seconds: float) -> ServiceHeartbeat:
    """``age_seconds`` saniye eski heartbeat üretir."""
    return ServiceHeartbeat(
        service_name=name,
        last_heartbeat_at=datetime.now(UTC) - timedelta(seconds=age_seconds),
    )


@pytest.mark.asyncio
async def test_check_heartbeats_classifies_states_correctly() -> None:
    """healthy / stale / down sınıflaması eşiklere uymalı."""
    db = _StubDB(
        rows=[
            _hb("custos-analytics", age_seconds=30),  # healthy
            _hb("custos-critical", age_seconds=120),  # stale
            _hb("legacy-service", age_seconds=300),  # down
        ]
    )
    healths = await check_heartbeats(db)  # type: ignore[arg-type]
    by_name = {h.service_name: h for h in healths}
    assert by_name["custos-analytics"].state == "healthy"
    assert by_name["custos-critical"].state == "stale"
    assert by_name["legacy-service"].state == "down"


@pytest.mark.asyncio
async def test_check_heartbeats_marks_missing_expected_as_down() -> None:
    """Beklenen servis tablo'da yoksa ``down`` (hiç çalışmamış) gibi raporlanır."""
    db = _StubDB(rows=[_hb("custos-analytics", 10)])
    healths = await check_heartbeats(
        db,  # type: ignore[arg-type]
        expected_services=["custos-analytics", "custos-critical"],
    )
    by_name = {h.service_name: h for h in healths}
    assert by_name["custos-critical"].state == "down"
    assert by_name["custos-critical"].age_seconds is None
    assert by_name["custos-critical"].last_heartbeat_at is None


@pytest.mark.asyncio
async def test_check_heartbeats_thresholds_boundary() -> None:
    """Sınır değerlerde sınıf doğru olmalı.

    Test framework'ünün yarattığı küçük zaman gecikmesini hesaba katmak için
    sınır değerlerden 2 sn aşağı/yukarı kullanılır.
    """
    db = _StubDB(
        rows=[
            _hb("svc-healthy", WARN_THRESHOLD_SECONDS - 2),
            _hb("svc-stale-low", WARN_THRESHOLD_SECONDS + 2),
            _hb("svc-stale-high", CRIT_THRESHOLD_SECONDS - 2),
            _hb("svc-down", CRIT_THRESHOLD_SECONDS + 2),
        ]
    )
    healths = await check_heartbeats(db)  # type: ignore[arg-type]
    by_name = {h.service_name: h for h in healths}
    assert by_name["svc-healthy"].state == "healthy"
    assert by_name["svc-stale-low"].state == "stale"
    assert by_name["svc-stale-high"].state == "stale"
    assert by_name["svc-down"].state == "down"


def test_overall_state_priority() -> None:
    """down > stale > healthy önceliği."""
    from custos.analytics.heartbeat import ServiceHealth

    # Hepsi healthy
    healthy = [
        ServiceHealth("a", datetime.now(UTC), 5, "healthy"),
        ServiceHealth("b", datetime.now(UTC), 10, "healthy"),
    ]
    assert overall_state(healthy) == "healthy"

    # Bir tanesi stale
    mixed = healthy + [ServiceHealth("c", datetime.now(UTC), 90, "stale")]
    assert overall_state(mixed) == "stale"

    # Bir tanesi down → her şeyi ezer
    with_down = mixed + [ServiceHealth("d", datetime.now(UTC), 999, "down")]
    assert overall_state(with_down) == "down"

    # Boş liste
    assert overall_state([]) == "unknown"
