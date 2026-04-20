"""Overview chart tag konfigurasyon CRUD entegrasyon testleri.

TimescaleDB'nin ayakta olmasini gerektirir (docker compose up -d).
"""

from __future__ import annotations

import asyncio

import pytest

from custos.shared.config import Settings
from custos.shared.database import (
    TagRecord,
    TimescaleDBDatabase,
)

# Bu testlerde kullanilan chart_key'ler. Migration 018 sonrasi
# overview_chart_tags.chart_key -> overview_charts.chart_key FK
# kisitina takilmamak icin fixture bu slotlari yaratir/temizler.
_TEST_CHART_KEYS: tuple[str, ...] = (
    "temp_chart",
    "pressure_chart",
    "rpm_chart",
    "flow_vibration_chart",
)


@pytest.fixture
def _check_db_available() -> None:
    """TimescaleDB erisilebilir degilse testi atla."""

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
        pytest.skip("TimescaleDB ayakta degil — 'docker compose up -d' calistir")


@pytest.fixture
async def db() -> TimescaleDBDatabase:
    """Test icin DB baglantisi olusturur, chart slotlarini hazirlar ve temizler."""
    s = Settings()
    database = TimescaleDBDatabase(s)
    await database.connect()
    pool = database._get_pool()
    # Test oncesi temizlik + test chart slotlarini yarat (FK icin gerekli).
    # sort_order bilinerek yuksek tutuldu ki production seed'in 0-5 araligiyla
    # celismesin; teardown'da CASCADE ile chart_tag'lar da silinir.
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM overview_chart_tags WHERE tag_id LIKE 'TEST_CHART_%'",
        )
        await conn.execute("DELETE FROM tags WHERE tag_id LIKE 'TEST_CHART_%'")
        for idx, ck in enumerate(_TEST_CHART_KEYS):
            await conn.execute(
                "INSERT INTO overview_charts (chart_key, title, sort_order) "
                "VALUES ($1, $2, $3) "
                "ON CONFLICT (chart_key) DO NOTHING",
                ck, f"Test {ck}", 1000 + idx,
            )
    yield database  # type: ignore[misc]
    # Test sonrasi temizlik: overview_charts silinince chart_tags FK CASCADE ile duser.
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM overview_charts WHERE chart_key = ANY($1::text[])",
            list(_TEST_CHART_KEYS),
        )
        await conn.execute("DELETE FROM tags WHERE tag_id LIKE 'TEST_CHART_%'")
    await database.close()


async def _create_test_tags(
    db: TimescaleDBDatabase,
    count: int = 3,
) -> list[str]:
    """Test icin tag'lar olusturur ve tag_id listesi dondurur."""
    tag_ids: list[str] = []
    for i in range(count):
        tag = await db.insert_tag(TagRecord(
            tag_id=f"TEST_CHART_{i:03d}",
            name=f"Test Chart Tag {i}",
            modbus_host="127.0.0.1",
            register_address=40001 + i,
        ))
        tag_ids.append(tag.tag_id)
    return tag_ids


@pytest.mark.usefixtures("_check_db_available")
async def test_replace_and_list_chart_tags(db: TimescaleDBDatabase) -> None:
    """replace_overview_chart_tags sonrasi list dogru donmeli."""
    tag_ids = await _create_test_tags(db, 3)

    result = await db.replace_overview_chart_tags("temp_chart", tag_ids)
    assert len(result) == 3
    assert [r.tag_id for r in result] == tag_ids
    assert all(r.chart_key == "temp_chart" for r in result)

    # Filtresiz list
    all_tags = await db.list_overview_chart_tags()
    temp_tags = [t for t in all_tags if t.chart_key == "temp_chart"]
    assert len(temp_tags) >= 3

    # Filtreli list
    filtered = await db.list_overview_chart_tags("temp_chart")
    assert len(filtered) >= 3
    assert all(f.chart_key == "temp_chart" for f in filtered)


@pytest.mark.usefixtures("_check_db_available")
async def test_replace_clears_old_tags(db: TimescaleDBDatabase) -> None:
    """replace_overview_chart_tags eski tag'lari temizlemeli."""
    tag_ids = await _create_test_tags(db, 3)

    # Ilk 3 tag'i ekle
    await db.replace_overview_chart_tags("pressure_chart", tag_ids)
    assert len(await db.list_overview_chart_tags("pressure_chart")) == 3

    # Sadece ilk 1 tag ile degistir — diger 2 silinmeli
    await db.replace_overview_chart_tags("pressure_chart", tag_ids[:1])
    result = await db.list_overview_chart_tags("pressure_chart")
    assert len(result) == 1
    assert result[0].tag_id == tag_ids[0]


@pytest.mark.usefixtures("_check_db_available")
async def test_replace_with_empty_list(db: TimescaleDBDatabase) -> None:
    """Bos liste ile replace tum tag'lari temizlemeli."""
    tag_ids = await _create_test_tags(db, 2)

    await db.replace_overview_chart_tags("rpm_chart", tag_ids)
    assert len(await db.list_overview_chart_tags("rpm_chart")) == 2

    await db.replace_overview_chart_tags("rpm_chart", [])
    assert len(await db.list_overview_chart_tags("rpm_chart")) == 0


@pytest.mark.usefixtures("_check_db_available")
async def test_sort_order_preserved(db: TimescaleDBDatabase) -> None:
    """Tag siralama sirasi korunmali."""
    tag_ids = await _create_test_tags(db, 3)

    # Ters sirada ekle
    reversed_ids = list(reversed(tag_ids))
    result = await db.replace_overview_chart_tags("flow_vibration_chart", reversed_ids)

    assert [r.tag_id for r in result] == reversed_ids
    assert [r.sort_order for r in result] == [0, 1, 2]


@pytest.mark.usefixtures("_check_db_available")
async def test_list_empty_chart_key(db: TimescaleDBDatabase) -> None:
    """Konfigurasyon olmayan chart_key icin bos liste donmeli."""
    result = await db.list_overview_chart_tags("nonexistent_chart")
    assert result == []
