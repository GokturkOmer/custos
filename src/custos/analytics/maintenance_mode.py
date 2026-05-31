"""Bakım modu — per-instance + global (V11-104, P-04).

Bakım sırasında alarm spam'ini önlemek ve eğitim setine kirli veri
girmemesi için iki seviye bakım modu:

- **Per-instance**: ``asset_instances.maintenance_mode_until`` doluyken o
  instance'ın tag'lerinden gelen breach'ler ``alarm_events.is_test=true``
  ile yazılır, push gönderilmez.
- **Global**: ``retention_config.global_maintenance_until`` doluyken tüm
  threshold breach'leri aynı şekilde işaretlenir (tatil / büyük bakım /
  eğitim için tek anahtar).

Süre dolunca ``expire_check_loop`` (her 60 sn arka plan task)
otomatik kapatır ve audit log'a ``stop_expired`` action'ı düşer.

K2 kararı: Operatör + Geliştirici her ikisi de global ve sınırsız bakıma
alabilir (route auth dependency'si ``require_operator``).
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime

import structlog

from custos.shared.database import (
    AssetInstance,
    AuditLogEntry,
    DatabaseInterface,
    RetentionConfig,
)
from custos.shared.maintenance import (
    is_global_maintenance as is_global_maintenance,
)
from custos.shared.maintenance import (
    is_instance_in_maintenance as is_instance_in_maintenance,
)
from custos.shared.maintenance import (
    now_or as _now_or,
)

logger = structlog.get_logger(logger_name="maintenance_mode")

# Audit log kategorisi — alarm_emergency / alarm pattern'iyle uyumlu.
AUDIT_CATEGORY: str = "maintenance_mode"

# Expire check loop default aralığı — 60 sn (prompt P-04). Süresi dolan
# bakım kayıtları en geç 1 dakika içinde otomatik kapanır; UI'da kullanıcı
# kapatmayı unutmuş olabilir, bu loop güvence sağlar.
DEFAULT_EXPIRE_CHECK_INTERVAL: float = 60.0


# ---------------------------------------------------------------------------
# Sorgular (read-only)
# ---------------------------------------------------------------------------
# ``now_or`` + ``is_instance_in_maintenance`` + ``is_global_maintenance`` artık
# ``shared/maintenance.py``'de (Critical loop ile paylaşılır — review H1 cutover).
# Yukarıda geriye-uyumlu import edildiler: ``maintenance_mode.is_global_maintenance``
# gibi mevcut çağrılar bozulmaz. State *değiştiren* işlemler bu modülde kalır.


async def get_active_maintenance_instances(
    db: DatabaseInterface,
    now: datetime | None = None,
) -> list[AssetInstance]:
    """UI banner / liste için: aktif bakım modunda olan instance'lar."""
    return await db.list_active_maintenance_instances(_now_or(now))


# ---------------------------------------------------------------------------
# State değişiklikleri (write)
# ---------------------------------------------------------------------------


def _audit_detail(payload: dict[str, object]) -> str:
    """Audit log detail'i için JSON encode (sorted, kararlı çıktı)."""
    return json.dumps(payload, sort_keys=True, default=str)


async def start_instance_maintenance(
    db: DatabaseInterface,
    instance_id: int,
    until: datetime | None,
    reason: str,
    user_id: int,
    now: datetime | None = None,
) -> AssetInstance:
    """Per-instance bakım modunu başlatır + audit log düşer.

    ``until=None`` manuel/sınırsız bakım — kullanıcı stop çağırana kadar açık.
    ``reason`` zorunlu (UI form'da min 3 karakter doğrulanır).
    """
    if not reason.strip():
        msg = "Bakım sebebi zorunlu"
        raise ValueError(msg)

    started = _now_or(now)
    updated = await db.update_asset_instance(
        instance_id,
        {
            "maintenance_mode_until": until,
            "maintenance_reason": reason,
            "maintenance_started_by_user_id": user_id,
            "maintenance_started_at": started,
        },
    )
    if updated is None:
        msg = f"Instance bulunamadı: {instance_id}"
        raise ValueError(msg)

    await db.insert_audit_log(
        AuditLogEntry(
            category=AUDIT_CATEGORY,
            action="start_instance",
            entity_type="asset_instance",
            entity_id=str(instance_id),
            detail=_audit_detail(
                {
                    "until": until,
                    "reason": reason,
                    "user_id": user_id,
                    "started_at": started,
                    "source": "manual",
                }
            ),
        ),
    )
    await logger.ainfo(
        "Per-instance bakım modu başlatıldı",
        instance_id=instance_id,
        until=until,
        reason=reason,
        user_id=user_id,
    )
    return updated


async def stop_instance_maintenance(
    db: DatabaseInterface,
    instance_id: int,
    user_id: int | None,
    source: str = "manual",
    now: datetime | None = None,
) -> AssetInstance | None:
    """Per-instance bakım modunu kapatır + audit log düşer.

    ``source='manual'`` (UI'dan kullanıcı kapattı) veya ``'expired'``
    (``expire_check_loop`` otomatik kapattı). ``user_id`` ikinci durumda
    None (sistem) olur.
    """
    instance_before = await db.get_asset_instance(instance_id)
    if instance_before is None:
        return None

    updated = await db.update_asset_instance(
        instance_id,
        {
            "maintenance_mode_until": None,
            "maintenance_reason": "",
            "maintenance_started_by_user_id": None,
            "maintenance_started_at": None,
        },
    )

    action = "stop_expired" if source == "expired" else "stop_manual"
    await db.insert_audit_log(
        AuditLogEntry(
            category=AUDIT_CATEGORY,
            action=action,
            entity_type="asset_instance",
            entity_id=str(instance_id),
            detail=_audit_detail(
                {
                    "previous_until": instance_before.maintenance_mode_until,
                    "previous_reason": instance_before.maintenance_reason,
                    "user_id": user_id,
                    "stopped_at": _now_or(now),
                    "source": source,
                }
            ),
        ),
    )
    await logger.ainfo(
        "Per-instance bakım modu kapandı",
        instance_id=instance_id,
        source=source,
        user_id=user_id,
    )
    return updated


async def start_global_maintenance(
    db: DatabaseInterface,
    until: datetime | None,
    reason: str,
    user_id: int,
    now: datetime | None = None,
) -> RetentionConfig:
    """Sistem-geneli bakım modunu başlatır + audit log düşer (K2)."""
    if not reason.strip():
        msg = "Bakım sebebi zorunlu"
        raise ValueError(msg)

    started = _now_or(now)
    cfg = await db.update_global_maintenance(
        until=until,
        reason=reason,
        user_id=user_id,
        started_at=started,
    )

    await db.insert_audit_log(
        AuditLogEntry(
            category=AUDIT_CATEGORY,
            action="start_global",
            entity_type="global",
            entity_id="global",
            detail=_audit_detail(
                {
                    "until": until,
                    "reason": reason,
                    "user_id": user_id,
                    "started_at": started,
                    "source": "manual",
                }
            ),
        ),
    )
    await logger.ainfo(
        "Global bakım modu başlatıldı",
        until=until,
        reason=reason,
        user_id=user_id,
    )
    return cfg


async def stop_global_maintenance(
    db: DatabaseInterface,
    user_id: int | None,
    source: str = "manual",
    now: datetime | None = None,
) -> RetentionConfig:
    """Global bakım modunu kapatır + audit log düşer."""
    cfg_before = await db.get_retention_config()

    cfg = await db.update_global_maintenance(
        until=None,
        reason="",
        user_id=None,
        started_at=None,
    )

    action = "stop_expired" if source == "expired" else "stop_manual"
    await db.insert_audit_log(
        AuditLogEntry(
            category=AUDIT_CATEGORY,
            action=action,
            entity_type="global",
            entity_id="global",
            detail=_audit_detail(
                {
                    "previous_until": cfg_before.global_maintenance_until,
                    "previous_reason": cfg_before.global_maintenance_reason,
                    "user_id": user_id,
                    "stopped_at": _now_or(now),
                    "source": source,
                }
            ),
        ),
    )
    await logger.ainfo(
        "Global bakım modu kapandı",
        source=source,
        user_id=user_id,
    )
    return cfg


# ---------------------------------------------------------------------------
# Expire check loop (arka plan task)
# ---------------------------------------------------------------------------


async def expire_once(
    db: DatabaseInterface,
    now: datetime | None = None,
) -> tuple[int, bool]:
    """Tek tarama: süresi dolmuş per-instance + global'i kapatır.

    Dönüş: ``(kapatılan_instance_sayısı, global_kapandı_mı)``.
    Test edilebilirlik için ``expire_check_loop`` dışı public.
    """
    moment = _now_or(now)

    # Per-instance: süresi dolanları kapat
    expired = await db.list_expired_maintenance_instances(moment)
    closed = 0
    for inst in expired:
        if inst.id is None:
            continue
        await stop_instance_maintenance(
            db,
            inst.id,
            user_id=None,
            source="expired",
            now=moment,
        )
        closed += 1

    # Global: süresi dolduysa kapat
    global_closed = False
    cfg = await db.get_retention_config()
    if (
        cfg.global_maintenance_until is not None
        and cfg.global_maintenance_until <= moment
    ):
        await stop_global_maintenance(
            db,
            user_id=None,
            source="expired",
            now=moment,
        )
        global_closed = True

    return closed, global_closed


async def expire_check_loop(
    db: DatabaseInterface,
    interval_seconds: float = DEFAULT_EXPIRE_CHECK_INTERVAL,
) -> None:
    """Arka plan task: her ``interval_seconds`` saniyede süresi dolan kayıtları kapatır.

    ``__main__.lifespan`` tarafından ``asyncio.create_task`` ile başlatılır;
    ``CancelledError`` ile temiz çıkış. Hatalar yutulup loglanır — sürekli
    çalışmaya devam eder (threshold_engine ile aynı dayanıklılık deseni).
    """
    await logger.ainfo(
        "Bakım modu expire check loop başlatıldı",
        interval_seconds=interval_seconds,
    )
    try:
        while True:
            try:
                closed, global_closed = await expire_once(db)
                if closed > 0 or global_closed:
                    await logger.ainfo(
                        "Süresi dolan bakım kayıtları kapatıldı",
                        instances_closed=closed,
                        global_closed=global_closed,
                    )
            except Exception:
                await logger.aerror(
                    "Bakım modu expire check'te hata",
                    exc_info=True,
                )
            try:
                await asyncio.sleep(interval_seconds)
            except asyncio.CancelledError:
                break
    finally:
        await logger.ainfo("Bakım modu expire check loop durdu")
