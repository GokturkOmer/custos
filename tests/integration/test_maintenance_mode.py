"""Bakım modu entegrasyon testleri (P-04 / V11-104).

TimescaleDB'nin ayakta olmasını gerektirir (docker compose up -d).

Senaryolar:
- Global bakım: tüm threshold breach'leri ``is_test=true``.
- Per-instance bakım: sadece o instance, diğerleri normal.
- expire_check_loop: süresi dolan kayıtlar otomatik kapatılır.
- Audit log: start/stop kayıtları.
- ``list_alarm_events(is_test=False)`` filtresi (P-12 hazırlık).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from custos.analytics import maintenance_mode
from custos.critical.threshold_watcher import ThresholdWatcher
from custos.shared.config import Settings
from custos.shared.database import (
    AssetInstance,
    TagBinding,
    TagReading,
    TagRecord,
    Threshold,
    TimescaleDBDatabase,
)


@pytest.fixture
def _check_db_available() -> None:
    """TimescaleDB erişilebilir değilse testi atla."""

    async def _probe() -> bool:
        s = Settings()
        db = TimescaleDBDatabase(s)
        try:
            await db.connect()
            result = await db.health_check()
            await db.close()
        except Exception:
            return False
        else:
            return result

    if not asyncio.run(_probe()):
        pytest.skip("TimescaleDB ayakta değil — 'docker compose up -d' çalıştır")


async def _cleanup(db: TimescaleDBDatabase) -> None:
    """Bakım test kayıtlarını + ilişkilerini temizler.

    FK sırası: alarm_events → tag_bindings → asset_instances → audit_log
    → thresholds → tags. Asset_templates dokunulmaz; AVM seed'inden
    geleni etkileme.
    """
    pool = db._get_pool()
    async with pool.acquire() as conn:
        # Audit (kategori bazlı temizlik)
        await conn.execute(
            "DELETE FROM audit_log WHERE category = 'maintenance_mode'",
        )
        await conn.execute(
            "DELETE FROM audit_log WHERE category = 'maintenance_test_alarm'",
        )
        # Alarm events
        await conn.execute(
            "DELETE FROM alarm_events WHERE tag_id LIKE 'TEST_MM_%'",
        )
        # Tag bindings of TEST instances
        await conn.execute(
            "DELETE FROM tag_bindings WHERE instance_id IN "
            "(SELECT id FROM asset_instances WHERE name LIKE 'TEST_MM_%')",
        )
        await conn.execute(
            "DELETE FROM asset_instances WHERE name LIKE 'TEST_MM_%'",
        )
        await conn.execute(
            "DELETE FROM thresholds WHERE tag_id LIKE 'TEST_MM_%'",
        )
        await conn.execute(
            "DELETE FROM tag_readings WHERE tag_id LIKE 'TEST_MM_%'",
        )
        await conn.execute(
            "DELETE FROM tags WHERE tag_id LIKE 'TEST_MM_%'",
        )
        # Global bakım modunu temizle (singleton tablo)
        await conn.execute(
            "UPDATE retention_config SET "
            "    global_maintenance_until = NULL, "
            "    global_maintenance_reason = '', "
            "    global_maintenance_started_by_user_id = NULL, "
            "    global_maintenance_started_at = NULL "
            "WHERE id = 1",
        )


@pytest.fixture
async def db() -> TimescaleDBDatabase:
    """Bakım test fixture — connect + cleanup before/after."""
    s = Settings()
    database = TimescaleDBDatabase(s)
    await database.connect()
    await _cleanup(database)
    yield database  # type: ignore[misc]
    await _cleanup(database)
    await database.close()


async def _seed_template(db: TimescaleDBDatabase) -> int:
    """Test için minimal template + role oluşturur, template_id döndürür."""
    pool = db._get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO asset_templates (slug, name, description, icon) "
            "VALUES ('test-mm-tmpl', 'Test MM Template', '', 'cpu') "
            "ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name "
            "RETURNING id",
        )
    assert row is not None
    return int(row["id"])


async def _setup_instance_with_threshold(
    db: TimescaleDBDatabase,
    template_id: int,
    instance_name: str,
    tag_id: str,
    set_point: float,
    breach_value: float,
) -> tuple[AssetInstance, Threshold]:
    """Asset instance + tag + binding + threshold oluşturur ve breach reading yazar."""
    instance = await db.insert_asset_instance(
        AssetInstance(template_id=template_id, name=instance_name)
    )
    assert instance.id is not None

    pool = db._get_pool()
    async with pool.acquire() as conn:
        role_row = await conn.fetchrow(
            "INSERT INTO template_roles (template_id, role_key, label) "
            "VALUES ($1, $2, 'Sıcaklık') "
            "ON CONFLICT (template_id, role_key) DO UPDATE SET label = EXCLUDED.label "
            "RETURNING id",
            template_id,
            f"role_{tag_id}",
        )
    assert role_row is not None

    existing_tag = await db.get_tag(tag_id)
    if existing_tag is None:
        await db.insert_tag(
            TagRecord(
                tag_id=tag_id,
                name=f"Test {tag_id}",
                modbus_host="127.0.0.1",
                register_address=40001,
            ),
        )

    await db.insert_tag_binding(
        TagBinding(
            instance_id=instance.id,
            role_id=int(role_row["id"]),
            tag_id=tag_id,
        ),
    )

    now = datetime.now(UTC)
    await db.insert_tag_readings_batch(
        [TagReading(timestamp=now, tag_id=tag_id, value=breach_value)]
    )

    threshold = await db.insert_threshold(
        Threshold(
            tag_id=tag_id,
            name=f"Threshold {tag_id}",
            direction="high",
            set_point=set_point,
            debounce_seconds=0,
        ),
    )
    return instance, threshold


@pytest.mark.usefixtures("_check_db_available")
async def test_global_maintenance_suppresses_alarms_to_is_test(
    db: TimescaleDBDatabase,
) -> None:
    """Global bakım açıkken üretilen alarm ``is_test=true`` ile yazılır."""
    template_id = await _seed_template(db)
    _, threshold = await _setup_instance_with_threshold(
        db,
        template_id=template_id,
        instance_name="TEST_MM_GLOBAL",
        tag_id="TEST_MM_TAG_GLOBAL",
        set_point=80.0,
        breach_value=95.0,
    )
    assert threshold.id is not None

    # Global bakım modunu başlat (24h)
    await maintenance_mode.start_global_maintenance(
        db,
        until=datetime.now(UTC) + timedelta(hours=24),
        reason="test global",
        user_id=1,
    )

    # Eşik alarm üretimi artık Critical loop'ta (ThresholdWatcher). Watcher
    # okumayı in-memory reading_source'tan alır; breach değeri burada taklit edilir.
    readings = {
        "TEST_MM_TAG_GLOBAL": TagReading(
            timestamp=datetime.now(UTC), tag_id="TEST_MM_TAG_GLOBAL", value=95.0,
        ),
    }
    watcher = ThresholdWatcher(db=db, reading_source=lambda: readings)
    await watcher._refresh_definitions()
    await watcher._evaluate_cycle()
    await watcher._evaluate_cycle()

    active = await db.get_active_alarm_for_threshold(threshold.id)
    assert active is not None
    assert active.is_test is True


@pytest.mark.usefixtures("_check_db_available")
async def test_instance_maintenance_suppresses_only_that_instance(
    db: TimescaleDBDatabase,
) -> None:
    """Per-instance bakım: sadece o instance'a bağlı tag is_test=True."""
    template_id = await _seed_template(db)
    inst_a, threshold_a = await _setup_instance_with_threshold(
        db,
        template_id=template_id,
        instance_name="TEST_MM_INST_A",
        tag_id="TEST_MM_TAG_A",
        set_point=80.0,
        breach_value=95.0,
    )
    assert inst_a.id is not None
    assert threshold_a.id is not None

    await maintenance_mode.start_instance_maintenance(
        db,
        instance_id=inst_a.id,
        until=datetime.now(UTC) + timedelta(hours=1),
        reason="test inst",
        user_id=1,
    )

    readings = {
        "TEST_MM_TAG_A": TagReading(
            timestamp=datetime.now(UTC), tag_id="TEST_MM_TAG_A", value=95.0,
        ),
    }
    watcher = ThresholdWatcher(db=db, reading_source=lambda: readings)
    await watcher._refresh_definitions()
    await watcher._evaluate_cycle()
    await watcher._evaluate_cycle()

    active_a = await db.get_active_alarm_for_threshold(threshold_a.id)
    assert active_a is not None
    assert active_a.is_test is True


@pytest.mark.usefixtures("_check_db_available")
async def test_other_instances_alarm_normally_during_per_instance_maintenance(
    db: TimescaleDBDatabase,
) -> None:
    """Bakımdaki instance is_test=True; diğer instance is_test=False."""
    template_id = await _seed_template(db)
    inst_a, threshold_a = await _setup_instance_with_threshold(
        db,
        template_id=template_id,
        instance_name="TEST_MM_OTHER_A",
        tag_id="TEST_MM_TAG_OA",
        set_point=80.0,
        breach_value=95.0,
    )
    inst_b, threshold_b = await _setup_instance_with_threshold(
        db,
        template_id=template_id,
        instance_name="TEST_MM_OTHER_B",
        tag_id="TEST_MM_TAG_OB",
        set_point=80.0,
        breach_value=95.0,
    )
    assert inst_a.id is not None and inst_b.id is not None
    assert threshold_a.id is not None and threshold_b.id is not None

    # Sadece A bakımda
    await maintenance_mode.start_instance_maintenance(
        db,
        instance_id=inst_a.id,
        until=datetime.now(UTC) + timedelta(hours=1),
        reason="A bakım",
        user_id=1,
    )

    readings = {
        "TEST_MM_TAG_OA": TagReading(
            timestamp=datetime.now(UTC), tag_id="TEST_MM_TAG_OA", value=95.0,
        ),
        "TEST_MM_TAG_OB": TagReading(
            timestamp=datetime.now(UTC), tag_id="TEST_MM_TAG_OB", value=95.0,
        ),
    }
    watcher = ThresholdWatcher(db=db, reading_source=lambda: readings)
    await watcher._refresh_definitions()
    await watcher._evaluate_cycle()
    await watcher._evaluate_cycle()

    alarm_a = await db.get_active_alarm_for_threshold(threshold_a.id)
    alarm_b = await db.get_active_alarm_for_threshold(threshold_b.id)
    assert alarm_a is not None and alarm_a.is_test is True
    assert alarm_b is not None and alarm_b.is_test is False


@pytest.mark.usefixtures("_check_db_available")
async def test_expire_check_auto_closes_after_until_timestamp(
    db: TimescaleDBDatabase,
) -> None:
    """Süresi dolmuş per-instance bakım expire_once ile otomatik kapanır."""
    template_id = await _seed_template(db)
    instance, _ = await _setup_instance_with_threshold(
        db,
        template_id=template_id,
        instance_name="TEST_MM_EXPIRE",
        tag_id="TEST_MM_TAG_EXP",
        set_point=80.0,
        breach_value=95.0,
    )
    assert instance.id is not None

    # Geçmiş bir until ile başlat (1 sn önce — süresi dolmuş)
    past = datetime.now(UTC) - timedelta(seconds=1)
    await maintenance_mode.start_instance_maintenance(
        db,
        instance_id=instance.id,
        until=past,
        reason="expired",
        user_id=1,
        now=past - timedelta(seconds=10),  # started_at past'ten önce
    )

    closed, _global_closed = await maintenance_mode.expire_once(db)
    assert closed == 1

    refreshed = await db.get_asset_instance(instance.id)
    assert refreshed is not None
    assert refreshed.maintenance_started_at is None
    assert refreshed.maintenance_mode_until is None


@pytest.mark.usefixtures("_check_db_available")
async def test_audit_log_records_start_stop(
    db: TimescaleDBDatabase,
) -> None:
    """Start ve stop sırasında audit_log'da maintenance_mode kategorisi düşer."""
    template_id = await _seed_template(db)
    instance, _ = await _setup_instance_with_threshold(
        db,
        template_id=template_id,
        instance_name="TEST_MM_AUDIT",
        tag_id="TEST_MM_TAG_AUD",
        set_point=80.0,
        breach_value=95.0,
    )
    assert instance.id is not None

    await maintenance_mode.start_instance_maintenance(
        db,
        instance_id=instance.id,
        until=datetime.now(UTC) + timedelta(hours=1),
        reason="audit test",
        user_id=1,
    )
    await maintenance_mode.stop_instance_maintenance(
        db,
        instance_id=instance.id,
        user_id=1,
        source="manual",
    )

    pool = db._get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT action FROM audit_log "
            "WHERE category = 'maintenance_mode' AND entity_id = $1 "
            "ORDER BY id",
            str(instance.id),
        )
    actions = [r["action"] for r in rows]
    assert "start_instance" in actions
    assert "stop_manual" in actions


@pytest.mark.usefixtures("_check_db_available")
async def test_anomaly_detector_excludes_is_test_alarms_from_training(
    db: TimescaleDBDatabase,
) -> None:
    """``list_alarm_events(is_test=False)`` bakım test alarm'ını filtreler.

    P-04 hazırlık: P-12'de anomaly detector eğitimi bu filtreyi kullanacak.
    Burada DB filtre kontratı doğrulanır.
    """
    template_id = await _seed_template(db)
    instance, threshold = await _setup_instance_with_threshold(
        db,
        template_id=template_id,
        instance_name="TEST_MM_FILTER",
        tag_id="TEST_MM_TAG_FILT",
        set_point=80.0,
        breach_value=95.0,
    )
    assert instance.id is not None
    assert threshold.id is not None

    await maintenance_mode.start_instance_maintenance(
        db,
        instance_id=instance.id,
        until=datetime.now(UTC) + timedelta(hours=1),
        reason="filter test",
        user_id=1,
    )

    readings = {
        "TEST_MM_TAG_FILT": TagReading(
            timestamp=datetime.now(UTC), tag_id="TEST_MM_TAG_FILT", value=95.0,
        ),
    }
    watcher = ThresholdWatcher(db=db, reading_source=lambda: readings)
    await watcher._refresh_definitions()
    await watcher._evaluate_cycle()
    await watcher._evaluate_cycle()

    # is_test=True alarm yazıldı
    test_alarms = await db.list_alarm_events(
        tag_id="TEST_MM_TAG_FILT",
        is_test=True,
    )
    real_alarms = await db.list_alarm_events(
        tag_id="TEST_MM_TAG_FILT",
        is_test=False,
    )
    assert len(test_alarms) >= 1
    assert len(real_alarms) == 0


@pytest.mark.usefixtures("_check_db_available")
async def test_push_skipped_when_is_test_true(
    db: TimescaleDBDatabase,
) -> None:
    """``send_push_notifications(is_test=True)`` erken dönüş (0 push)."""
    from custos.analytics.push_sender import send_push_notifications

    sent = await send_push_notifications(
        db=db,
        title="bakım test",
        body="atlanmalı",
        severity="warn",
        is_test=True,
    )
    assert sent == 0
