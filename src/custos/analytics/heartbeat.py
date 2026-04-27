"""Cross-service watchdog heartbeat modülü (V11-105/K13).

İki sorumluluk:
1. ``write_heartbeat`` — Servisin DB'ye periyodik canlılık kaydı.
2. ``check_heartbeats`` — Tüm servislerin son heartbeat'lerini okuyup
   stale durumda olanları (>180s) flag'ler. Analytics loop bu sonucu
   alarm üretmek ve dashboard widget'ı beslemek için kullanır.

Bu modül **analytics tarafında** durur (kritik döngü kütüphane bağımlılığı
minimum) ama her iki süreç de import edebilir — sadece DB arayüzüne
ihtiyacı vardır, ML import yoktur.

Tasarım kararı: heartbeat tablosu tek satırlıdır (PK service_name,
upsert) — büyümez, retention gerekmez. systemd watchdog ayrı katman.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

from custos.shared.database import DatabaseInterface, ServiceHeartbeat

logger = structlog.get_logger(logger_name="heartbeat")

# Eşikler — pilot uptime ≥%99 için makul değerler.
# 60s yeşil, 60-180s sarı (geçici tıkanma), >180s kırmızı (alarm).
WARN_THRESHOLD_SECONDS: int = 60
CRIT_THRESHOLD_SECONDS: int = 180


@dataclass
class ServiceHealth:
    """Bir servisin watchdog sağlık değerlendirmesi.

    ``state``:
      - ``healthy``  : son heartbeat ≤60s
      - ``stale``    : 60-180s (geçici tıkanma)
      - ``down``     : >180s veya hiç heartbeat yok (kritik)
    """

    service_name: str
    last_heartbeat_at: datetime | None
    age_seconds: float | None  # None: hiç heartbeat yok
    state: str  # 'healthy' / 'stale' / 'down'
    metadata: dict[str, Any] | None = None


async def write_heartbeat(
    db: DatabaseInterface,
    service_name: str,
    status: str = "active",
    metadata: dict[str, Any] | None = None,
) -> None:
    """Servis heartbeat'ini DB'ye yazar (upsert).

    Hata durumunda log atar ama exception fırlatmaz — heartbeat yazımı
    başarısızsa bile servisin ana loop'u devam etmeli (watchdog dış
    katman olarak yine cross-check ile alarm üretir).
    """
    try:
        await db.write_service_heartbeat(service_name, status, metadata)
    except Exception:
        await logger.awarning(
            "Heartbeat yazılamadı",
            service_name=service_name,
            exc_info=True,
        )


async def check_heartbeats(
    db: DatabaseInterface,
    expected_services: list[str] | None = None,
) -> list[ServiceHealth]:
    """Tüm servislerin sağlığını değerlendirir.

    ``expected_services`` verilirse: bu listede olup DB'de heartbeat'i
    olmayan servisler "down" olarak raporlanır (hiç başlamamış olabilir).
    Verilmezse sadece DB'deki kayıtlar değerlendirilir.
    """
    rows = await db.list_service_heartbeats()
    by_name: dict[str, ServiceHeartbeat] = {r.service_name: r for r in rows}
    now = datetime.now(UTC)

    candidates = (
        list({*expected_services, *by_name.keys()})
        if expected_services
        else list(by_name.keys())
    )

    results: list[ServiceHealth] = []
    for name in sorted(candidates):
        row = by_name.get(name)
        if row is None:
            results.append(
                ServiceHealth(
                    service_name=name,
                    last_heartbeat_at=None,
                    age_seconds=None,
                    state="down",
                    metadata=None,
                )
            )
            continue

        last_at = row.last_heartbeat_at
        age = (now - last_at).total_seconds()
        if age <= WARN_THRESHOLD_SECONDS:
            state = "healthy"
        elif age <= CRIT_THRESHOLD_SECONDS:
            state = "stale"
        else:
            state = "down"

        results.append(
            ServiceHealth(
                service_name=name,
                last_heartbeat_at=last_at,
                age_seconds=age,
                state=state,
                metadata=row.metadata,
            )
        )

    return results


def overall_state(healths: list[ServiceHealth]) -> str:
    """Servis listesinden global durum türetir.

    - ``down``    : herhangi bir servis down → kırmızı
    - ``stale``   : herhangi bir servis stale → sarı
    - ``healthy`` : hepsi healthy → yeşil
    - ``unknown`` : hiç servis yoksa
    """
    if not healths:
        return "unknown"
    states = {h.state for h in healths}
    if "down" in states:
        return "down"
    if "stale" in states:
        return "stale"
    return "healthy"


def stale_age(threshold_seconds: int = CRIT_THRESHOLD_SECONDS) -> timedelta:
    """Test ve mock için yardımcı: timedelta versiyonu."""
    return timedelta(seconds=threshold_seconds)
