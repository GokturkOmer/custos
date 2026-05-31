"""Bakım modu gerçek-zamanlı kontrolleri — süreç-bağımsız (shared).

"Bu instance / sistem şu an bakım modunda mı?" sorularının cevabı hem Analytics
(``threshold_engine`` rate/cross_sensor yolu, ``maintenance_mode`` state
değişiklikleri) hem Critical (``threshold_watcher`` — eşik breach'inde is_test
kararı, review H1 cutover) tarafından gerekiyor. Bu yüzden bu okuma-amaçlı
kontroller ``shared/``'a konur (yalnız ``DatabaseInterface``'e ihtiyaçları var,
SQL/ML yok) — Critical'ın Analytics'i import etmesini önler.

State *değiştiren* bakım işlemleri (start/stop/expire) ve UI yardımcıları
Analytics'e özgü olduğundan ``analytics/maintenance_mode.py``'de kalır; o modül
buradaki kontrolleri geriye-uyumlu olarak re-export eder.
"""

from __future__ import annotations

from datetime import UTC, datetime

from custos.shared.database import DatabaseInterface


def now_or(now: datetime | None) -> datetime:
    """``now`` opsiyonel; verilmediyse UTC şimdiki zamanı döner."""
    return now if now is not None else datetime.now(UTC)


def _is_window_active(
    until: datetime | None,
    started_at: datetime | None,
    now: datetime,
) -> bool:
    """Bakım penceresi aktif mi? (P-04 ortak kontrolü).

    Aktif: ``started_at`` set EDİLMİŞ ve (``until`` None — sınırsız/manuel
    kapatma — VEYA ``until > now``).
    """
    if started_at is None:
        return False
    if until is None:
        # Manuel/sınırsız bakım — kullanıcı kapatana kadar açık
        return True
    return until > now


async def is_instance_in_maintenance(
    db: DatabaseInterface,
    instance_id: int,
    now: datetime | None = None,
) -> bool:
    """Verilen instance bakım modunda mı?"""
    instance = await db.get_asset_instance(instance_id)
    if instance is None:
        return False
    return _is_window_active(
        instance.maintenance_mode_until,
        instance.maintenance_started_at,
        now_or(now),
    )


async def is_global_maintenance(
    db: DatabaseInterface,
    now: datetime | None = None,
) -> bool:
    """Sistem-geneli bakım modunda mı?"""
    cfg = await db.get_retention_config()
    return _is_window_active(
        cfg.global_maintenance_until,
        cfg.global_maintenance_started_at,
        now_or(now),
    )


__all__ = [
    "is_global_maintenance",
    "is_instance_in_maintenance",
    "now_or",
]
