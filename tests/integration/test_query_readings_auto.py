"""query_readings_auto (F11 Paket C) integration testleri.

`query_readings_auto` pencere büyüklüğüne göre üç katmandan birini seçer:
ham `tag_readings`, `tag_readings_1min`, veya `tag_readings_1hour`. Testler
iki şeyi doğrular: (a) dispatch — doğru katman çağrılıyor mu, (b) çıktı —
homojen `list[TagReading]` ve target_points sınırına uyuyor mu.

Katman ayrımı için monkeypatch spy yaklaşımı kullanıyoruz; karar mantığı
çekirdek API, refactor sırasında stabil kalacak.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest

from custos.shared.database import (
    TagReading,
    TagRecord,
    TimescaleDBDatabase,
)

# Spy helper tipleri — async private helper imzasıyla aynı.
_HelperFn = Callable[..., Awaitable[list[TagReading]]]


def _install_dispatch_spy(
    db: TimescaleDBDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> list[str]:
    """Üç private helper'ı sar, hangisinin çağrıldığını kayıt et.

    Dönen liste test içinde assert edilir: ['raw'] / ['1min'] / ['1hour'].
    """
    calls: list[str] = []
    orig_raw: _HelperFn = db._query_raw_downsampled
    orig_1min: _HelperFn = db._query_1min_downsampled
    orig_1hour: _HelperFn = db._query_1hour_downsampled

    async def spy_raw(*args: object, **kwargs: object) -> list[TagReading]:
        calls.append("raw")
        return await orig_raw(*args, **kwargs)

    async def spy_1min(*args: object, **kwargs: object) -> list[TagReading]:
        calls.append("1min")
        return await orig_1min(*args, **kwargs)

    async def spy_1hour(*args: object, **kwargs: object) -> list[TagReading]:
        calls.append("1hour")
        return await orig_1hour(*args, **kwargs)

    monkeypatch.setattr(db, "_query_raw_downsampled", spy_raw)
    monkeypatch.setattr(db, "_query_1min_downsampled", spy_1min)
    monkeypatch.setattr(db, "_query_1hour_downsampled", spy_1hour)
    return calls


async def _seed_raw_readings(
    db: TimescaleDBDatabase,
    tag_id: str,
    start: datetime,
    count: int,
    step_seconds: float,
    base_value: float = 0.0,
) -> None:
    """N adet okuma insert eder (her biri step_seconds arayla, i+base_value)."""
    batch: list[TagReading] = [
        TagReading(
            timestamp=start + timedelta(seconds=i * step_seconds),
            tag_id=tag_id,
            value=float(i) + base_value,
            quality_flag=0,
        )
        for i in range(count)
    ]
    await db.insert_tag_readings_batch(batch)


async def _refresh_1min(
    db: TimescaleDBDatabase,
    start: datetime,
    end: datetime,
) -> None:
    """1min CA'yı verilen aralık için manuel refresh eder.

    `CALL refresh_continuous_aggregate` açık transaction içinde çalışamaz;
    asyncpg implicit txn'ı COMMIT ile kapatıyoruz. PROCEDURE param tipleri
    çıkarılamadığı için explicit cast zorunlu.
    """
    pool = db._get_pool()
    async with pool.acquire() as conn:
        await conn.execute("COMMIT;")
        await conn.execute(
            "CALL refresh_continuous_aggregate("
            "    'tag_readings_1min', $1::timestamptz, $2::timestamptz"
            ");",
            start,
            end,
        )


# --- Dispatch testleri (spy) ---


@pytest.mark.usefixtures("_check_db_available")
async def test_short_window_hits_raw_table(
    db: TimescaleDBDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """30 dakika pencere → ham tablodan (_query_raw_downsampled) okunur."""
    calls = _install_dispatch_spy(db, monkeypatch)
    end = datetime.now(UTC)
    start = end - timedelta(minutes=30)

    await db.query_readings_auto("TEST_NONEXISTENT_SHORT", start, end)

    assert calls == ["raw"]


@pytest.mark.usefixtures("_check_db_available")
async def test_medium_window_hits_1min_agg(
    db: TimescaleDBDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """4 saat pencere → 1min aggregate'ten (_query_1min_downsampled) okunur."""
    calls = _install_dispatch_spy(db, monkeypatch)
    end = datetime.now(UTC)
    start = end - timedelta(hours=4)

    await db.query_readings_auto("TEST_NONEXISTENT_MED", start, end)

    assert calls == ["1min"]


@pytest.mark.usefixtures("_check_db_available")
async def test_long_window_hits_1hour_agg(
    db: TimescaleDBDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """1 hafta pencere → 1hour aggregate'ten (_query_1hour_downsampled) okunur."""
    calls = _install_dispatch_spy(db, monkeypatch)
    end = datetime.now(UTC)
    start = end - timedelta(days=7)

    await db.query_readings_auto("TEST_NONEXISTENT_LONG", start, end)

    assert calls == ["1hour"]


# --- Sınır (boundary) testleri — off-by-one hatalarını yakalar ---


@pytest.mark.usefixtures("_check_db_available")
async def test_boundary_1h_exact_hits_raw(
    db: TimescaleDBDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tam 1 saat pencere → ham katmana gider (inclusive eşik)."""
    calls = _install_dispatch_spy(db, monkeypatch)
    end = datetime.now(UTC)
    start = end - timedelta(hours=1)

    await db.query_readings_auto("TEST_NONEXISTENT_B1H", start, end)

    assert calls == ["raw"]


@pytest.mark.usefixtures("_check_db_available")
async def test_boundary_1d_exact_hits_1min(
    db: TimescaleDBDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tam 1 gün pencere → 1min aggregate'e gider (inclusive eşik)."""
    calls = _install_dispatch_spy(db, monkeypatch)
    end = datetime.now(UTC)
    start = end - timedelta(days=1)

    await db.query_readings_auto("TEST_NONEXISTENT_B1D", start, end)

    assert calls == ["1min"]


# --- Çıktı testleri (gerçek veri) ---


@pytest.mark.usefixtures("_check_db_available")
async def test_return_points_under_target(
    db: TimescaleDBDatabase,
) -> None:
    """Ham katmanda 1000 okuma, target=600 → dönen <= 600."""
    unique = uuid.uuid4().hex[:8]
    tag_id = f"TEST_AUTO_TARGET_{unique}"
    await db.insert_tag(
        TagRecord(
            tag_id=tag_id,
            name="Auto Target",
            modbus_host="127.0.0.1",
            register_address=41000,
        )
    )

    end = datetime.now(UTC)
    start = end - timedelta(minutes=50)  # ham katmana düşsün
    await _seed_raw_readings(
        db,
        tag_id,
        start,
        count=1000,
        step_seconds=3.0,
    )

    result = await db.query_readings_auto(
        tag_id,
        start,
        end,
        target_points=600,
    )
    # target_points = 600 soft hedef; time_bucket epoch'a hizalandığı için
    # pencere bucket sınırlarına denk gelmezse 1 bucket daha dönebilir.
    assert len(result) <= 601
    assert len(result) > 0


@pytest.mark.usefixtures("_check_db_available")
async def test_homogeneous_output_type(
    db: TimescaleDBDatabase,
) -> None:
    """Her üç katmandan dönen tip `list[TagReading]` — tüketici fark etmez.

    Ham + 1min katmanlarında gerçek veri (element tip doğrulamak için);
    1hour katmanında boş liste de kabul — boş `list[TagReading]` hâlâ
    homojen tiptir, örnek yoksa eleman kontrolü atlanır.
    """
    unique = uuid.uuid4().hex[:8]
    tag_id = f"TEST_AUTO_HOMO_{unique}"
    await db.insert_tag(
        TagRecord(
            tag_id=tag_id,
            name="Auto Homo",
            modbus_host="127.0.0.1",
            register_address=41001,
        )
    )

    # 1min CA refresh için dakika başına hizalı bir bucket hazırla.
    now = datetime.now(UTC).replace(microsecond=0)
    bucket_start = now.replace(second=0) - timedelta(minutes=5)
    await _seed_raw_readings(
        db,
        tag_id,
        bucket_start,
        count=60,
        step_seconds=1.0,
    )
    await _refresh_1min(db, bucket_start, bucket_start + timedelta(minutes=2))

    end = datetime.now(UTC)
    short_start = end - timedelta(minutes=30)  # → raw
    medium_start = end - timedelta(hours=4)  # → 1min
    long_start = end - timedelta(days=7)  # → 1hour

    results_raw = await db.query_readings_auto(tag_id, short_start, end)
    results_1min = await db.query_readings_auto(tag_id, medium_start, end)
    results_1hour = await db.query_readings_auto(tag_id, long_start, end)

    for result in (results_raw, results_1min, results_1hour):
        assert isinstance(result, list)
        for element in result:
            assert isinstance(element, TagReading)
            assert element.tag_id == tag_id

    # Ham ve 1min için en az bir satır gelmeli — gerçek element tipi sınandı.
    assert len(results_raw) > 0, "ham katman boş — seed/pencere uyumsuz"
    assert len(results_1min) > 0, "1min katman boş — refresh başarısız"


@pytest.mark.usefixtures("_check_db_available")
async def test_empty_result_for_tag_with_no_data(
    db: TimescaleDBDatabase,
) -> None:
    """Tanımlı ama veri eklenmemiş tag → boş liste (tüm katmanlarda)."""
    unique = uuid.uuid4().hex[:8]
    tag_id = f"TEST_AUTO_EMPTY_{unique}"
    await db.insert_tag(
        TagRecord(
            tag_id=tag_id,
            name="Auto Empty",
            modbus_host="127.0.0.1",
            register_address=41002,
        )
    )

    end = datetime.now(UTC)

    assert (
        await db.query_readings_auto(
            tag_id,
            end - timedelta(minutes=30),
            end,
        )
        == []
    )
    assert (
        await db.query_readings_auto(
            tag_id,
            end - timedelta(hours=4),
            end,
        )
        == []
    )
    assert (
        await db.query_readings_auto(
            tag_id,
            end - timedelta(days=7),
            end,
        )
        == []
    )
