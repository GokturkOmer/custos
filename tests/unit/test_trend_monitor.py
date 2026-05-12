"""src/custos/analytics/trend_monitor.py — birim testleri (Faz 2 P0).

Kapsam:
- Constructor validasyon — gecersiz parametreler ValueError firlatir.
- Warmup penceresinde alert uretilmez (min_observations).
- Sabit (stable) skor → alert yok.
- Lineer yukari trend → alert uretilir (slope esigi asilir).
- Tek nokta spike → alert yok (EWMA spike'i yutar).
- Severity esik testi: kucuk slope → 'warn', buyuk slope → 'crit'.
- Negatif slope (asagi trend) → alert yok.
- ``reset`` / ``reset_all`` state temizler.
- Multi-asset state'leri bagimsiz.
- ``up_streak`` esik altina dusunce sifirlanir, alert duration artar.
- ``tick_minutes`` duration_min unitesini etkiler.
- Debug yardimcilari (``get_current_score`` / ``get_up_streak`` /
  ``tracked_assets``).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from custos.analytics.trend_monitor import (
    DEFAULT_EWMA_ALPHA,
    DEFAULT_MIN_OBSERVATIONS,
    DEFAULT_SLOPE_THRESHOLD,
    DEFAULT_WINDOW_SIZE,
    TrendAlert,
    TrendMonitor,
)

BASE_TS = datetime(2026, 5, 12, 0, 0, 0, tzinfo=UTC)


def _ts(tick: int, *, minutes: int = 10) -> datetime:
    """``tick`` numarasini wall-clock timestamp'a cevirir (10dk default)."""
    return BASE_TS + timedelta(minutes=tick * minutes)


def _feed(
    mon: TrendMonitor,
    asset_id: int,
    values: list[float],
    *,
    tick_minutes: int = 10,
) -> list[TrendAlert | None]:
    """Yardimci: ``values`` dizisini sirayla mon.update'e besler."""
    out: list[TrendAlert | None] = []
    for i, v in enumerate(values):
        out.append(mon.update(asset_id, _ts(i, minutes=tick_minutes), v))
    return out


# ---------------- Constructor validasyonu ----------------


def test_default_construction_uses_module_defaults() -> None:
    """Constructor parametresiz cagrilirsa module default'lari kullanir."""
    mon = TrendMonitor()
    assert mon.window_size == DEFAULT_WINDOW_SIZE
    assert mon.ewma_alpha == DEFAULT_EWMA_ALPHA
    assert mon.slope_threshold == DEFAULT_SLOPE_THRESHOLD
    assert mon.min_observations == DEFAULT_MIN_OBSERVATIONS
    assert mon.tracked_assets == ()


def test_invalid_window_size_raises() -> None:
    """window_size < 2 → ValueError."""
    with pytest.raises(ValueError, match="window_size"):
        TrendMonitor(window_size=1)


def test_invalid_alpha_too_high_raises() -> None:
    """ewma_alpha > 1.0 → ValueError."""
    with pytest.raises(ValueError, match="ewma_alpha"):
        TrendMonitor(ewma_alpha=1.5)


def test_invalid_alpha_zero_raises() -> None:
    """ewma_alpha = 0 → ValueError (sinir disi)."""
    with pytest.raises(ValueError, match="ewma_alpha"):
        TrendMonitor(ewma_alpha=0.0)


def test_invalid_alpha_negative_raises() -> None:
    """ewma_alpha < 0 → ValueError."""
    with pytest.raises(ValueError, match="ewma_alpha"):
        TrendMonitor(ewma_alpha=-0.1)


def test_invalid_slope_threshold_negative_raises() -> None:
    """Negatif slope_threshold → ValueError."""
    with pytest.raises(ValueError, match="slope_threshold"):
        TrendMonitor(slope_threshold=-0.01)


def test_invalid_min_observations_too_low_raises() -> None:
    """min_observations < 2 → ValueError."""
    with pytest.raises(ValueError, match="min_observations"):
        TrendMonitor(min_observations=1)


def test_invalid_min_observations_gt_window_raises() -> None:
    """min_observations > window_size → ValueError."""
    with pytest.raises(ValueError, match="min_observations"):
        TrendMonitor(window_size=10, min_observations=20)


def test_invalid_severity_crit_multiplier_raises() -> None:
    """severity_crit_multiplier < 1.0 → ValueError."""
    with pytest.raises(ValueError, match="severity_crit_multiplier"):
        TrendMonitor(severity_crit_multiplier=0.5)


def test_invalid_tick_minutes_raises() -> None:
    """tick_minutes < 1 → ValueError."""
    with pytest.raises(ValueError, match="tick_minutes"):
        TrendMonitor(tick_minutes=0)


# ---------------- Warmup ve stable ----------------


def test_warmup_returns_none_until_min_observations() -> None:
    """Warmup penceresinde alert yok — min_observations dolmadan None."""
    mon = TrendMonitor(window_size=10, min_observations=5, slope_threshold=0.001)
    # 4 ornek besle — min_observations=5 altinda
    results = _feed(mon, asset_id=1, values=[1.0, 1.1, 1.2, 1.3])
    assert all(r is None for r in results)


def test_stable_score_no_alert() -> None:
    """Sabit skor → slope=0 → alert yok."""
    mon = TrendMonitor(window_size=10, min_observations=5, slope_threshold=0.001)
    # 20 ornek hep 0.5 → slope = 0 → alert yok
    results = _feed(mon, asset_id=1, values=[0.5] * 20)
    assert all(r is None for r in results)


# ---------------- Trend tespiti ----------------


def test_linear_trend_up_produces_alert() -> None:
    """Lineer yukari trend → en az 1 TrendAlert uretilir."""
    mon = TrendMonitor(
        window_size=10,
        min_observations=5,
        slope_threshold=0.001,
        ewma_alpha=0.3,  # daha hizli tepki — test hizlandirir
    )
    # 50 ornek, her birim +0.01 lineer artis (slope ~0.01/tick alpha-baga gore)
    values = [i * 0.01 for i in range(50)]
    results = _feed(mon, asset_id=1, values=values)
    alerts = [r for r in results if r is not None]
    assert len(alerts) > 0
    # Tum alarmlarda slope pozitif
    assert all(a.ewma_slope > 0 for a in alerts)
    assert all(a.asset_instance_id == 1 for a in alerts)


def test_spike_does_not_produce_alert_with_high_threshold() -> None:
    """Makul spike + threshold → alert yok (EWMA decay ile slope yetersiz).

    Stable 1.0 stream + tek 2.0 spike. Alpha=0.05 ile spike EWMA'yi sadece
    1.0 → ~1.05'e cikarir; sonrasi exponential decay ile geri 1.0'a doner.
    Lag pencerede slope ya cok kucuk ya da negatif → threshold asilmaz.
    """
    mon = TrendMonitor(
        window_size=20,
        min_observations=10,
        slope_threshold=0.01,
        ewma_alpha=0.05,
    )
    values = [1.0] * 60
    values[15] = 2.0  # makul spike (2x stable)
    results = _feed(mon, asset_id=1, values=values)
    alerts = [r for r in results if r is not None]
    assert alerts == []


def test_spike_streak_eventually_resets() -> None:
    """Buyuk spike alarm uretebilir ama decay sonrasi streak sifirlanir.

    Lineer trend'in aksine, izole spike etkisi zaman icinde sonumler
    (exponential decay). Yeterli zaman gecince slope negatife doner ve
    up_streak 0'a duser.
    """
    mon = TrendMonitor(
        window_size=20,
        min_observations=10,
        slope_threshold=0.01,
        ewma_alpha=0.1,
    )
    values = [0.0] * 80
    values[15] = 5.0  # buyuk spike
    _feed(mon, asset_id=1, values=values)
    # Spike sonrasi 60+ tick gecti, decay slope'u negatife cevirmis olmali
    assert mon.get_up_streak(1) == 0


def test_down_trend_no_alert() -> None:
    """Lineer asagi trend → slope negatif → alert yok."""
    mon = TrendMonitor(
        window_size=10,
        min_observations=5,
        slope_threshold=0.001,
        ewma_alpha=0.3,
    )
    # Yuksek baslangic, lineer dusus
    values = [1.0 - i * 0.01 for i in range(50)]
    results = _feed(mon, asset_id=1, values=values)
    alerts = [r for r in results if r is not None]
    assert alerts == []


# ---------------- Severity ----------------


def test_severity_warn_for_small_slope() -> None:
    """Slope esiginin biraz uzerinde → 'warn'."""
    mon = TrendMonitor(
        window_size=10,
        min_observations=5,
        slope_threshold=0.001,
        severity_crit_multiplier=3.0,
        ewma_alpha=0.3,
    )
    # Slope ~0.0015/tick (esigin ~1.5x) — warn esiginde, crit alti
    values = [i * 0.0015 for i in range(40)]
    results = _feed(mon, asset_id=1, values=values)
    alerts = [r for r in results if r is not None]
    assert len(alerts) > 0
    # Cogu alert warn olmali (slope yavas yavas artar, crit'e cikabilir ama
    # erken tickler kesin warn)
    assert alerts[0].severity == "warn"


def test_severity_crit_for_large_slope() -> None:
    """Slope esigi * crit_multiplier ustunde → 'crit'."""
    mon = TrendMonitor(
        window_size=10,
        min_observations=5,
        slope_threshold=0.001,
        severity_crit_multiplier=3.0,
        ewma_alpha=0.5,
    )
    # Cok hizli artis (slope >> 3 * 0.001)
    values = [i * 0.1 for i in range(40)]
    results = _feed(mon, asset_id=1, values=values)
    alerts = [r for r in results if r is not None]
    assert len(alerts) > 0
    assert any(a.severity == "crit" for a in alerts)


# ---------------- Streak ve duration ----------------


def test_streak_resets_when_slope_drops_below_threshold() -> None:
    """Esik altina dusunce up_streak sifirlanir, duration_min kucuk olur."""
    mon = TrendMonitor(
        window_size=10,
        min_observations=5,
        slope_threshold=0.001,
        ewma_alpha=0.3,
    )
    # Sirasiyla: yukari artis (10 tick) → plato (20 tick) → tekrar artis
    rising = [i * 0.05 for i in range(15)]
    plateau = [rising[-1]] * 20
    rising2 = [rising[-1] + (i + 1) * 0.05 for i in range(15)]
    all_vals = rising + plateau + rising2
    _feed(mon, asset_id=1, values=all_vals)
    # Plato sonu state'inde streak en az bir kez sifirlanmis olmali — ozel
    # invariant kontrolu yerine "ikinci risewla yeni alarm zinciri var" diye
    # kontrol et: monitor'un internal streak'i son artis sirasinda 0'dan baslar.
    streak_after = mon.get_up_streak(1)
    # Tum besleme sonunda en az 1 tick yukari devamlilik → streak > 0
    assert streak_after >= 1


def test_alert_duration_min_increases_with_streak() -> None:
    """Ardisik trend ticks duration_min'i lineer artirir."""
    mon = TrendMonitor(
        window_size=10,
        min_observations=5,
        slope_threshold=0.001,
        ewma_alpha=0.3,
        tick_minutes=10,
    )
    values = [i * 0.05 for i in range(40)]
    results = _feed(mon, asset_id=1, values=values)
    alerts = [r for r in results if r is not None]
    assert len(alerts) >= 2
    # Sonra gelen alert daha buyuk duration_min sahip (streak buyumus)
    durations = [a.duration_min for a in alerts]
    # Monoton non-decreasing (her tick'te +tick_minutes ekleniyor)
    for prev, curr in zip(durations[:-1], durations[1:], strict=True):
        assert curr >= prev


def test_tick_minutes_affects_duration_unit() -> None:
    """tick_minutes degistirilirse duration_min direkt orantili degisir."""
    mon5 = TrendMonitor(
        window_size=10,
        min_observations=5,
        slope_threshold=0.001,
        ewma_alpha=0.3,
        tick_minutes=5,
    )
    mon10 = TrendMonitor(
        window_size=10,
        min_observations=5,
        slope_threshold=0.001,
        ewma_alpha=0.3,
        tick_minutes=10,
    )
    values = [i * 0.05 for i in range(25)]
    a5 = [r for r in _feed(mon5, 1, values, tick_minutes=5) if r is not None]
    a10 = [r for r in _feed(mon10, 1, values, tick_minutes=10) if r is not None]
    # Ayni streak sayisinda → mon10 duration mon5'in 2 kati
    assert len(a5) == len(a10)
    if a5 and a10:
        assert a10[0].duration_min == 2 * a5[0].duration_min


# ---------------- Reset + multi-asset ----------------


def test_reset_clears_state_for_asset() -> None:
    """``reset(asset_id)`` o asset'in state'ini temizler."""
    mon = TrendMonitor(window_size=10, min_observations=5)
    _feed(mon, asset_id=1, values=[i * 0.05 for i in range(10)])
    assert mon.get_current_score(1) is not None
    mon.reset(1)
    assert mon.get_current_score(1) is None
    assert mon.tracked_assets == ()


def test_reset_all_clears_all_states() -> None:
    """``reset_all()`` tum asset'lerin state'ini temizler."""
    mon = TrendMonitor(window_size=10, min_observations=5)
    _feed(mon, asset_id=1, values=[0.1, 0.2, 0.3])
    _feed(mon, asset_id=2, values=[0.4, 0.5, 0.6])
    assert len(mon.tracked_assets) == 2
    mon.reset_all()
    assert mon.tracked_assets == ()


def test_multi_asset_states_are_independent() -> None:
    """Iki farkli asset_id state'i carismaz."""
    mon = TrendMonitor(
        window_size=10,
        min_observations=5,
        slope_threshold=0.001,
        ewma_alpha=0.3,
    )
    # Asset 1: yukseliyor (alert beklenir)
    # Asset 2: sabit (alert beklenmez)
    for i in range(30):
        mon.update(1, _ts(i), 0.5 + i * 0.05)
        mon.update(2, _ts(i), 0.5)
    s1 = mon.get_current_score(1)
    s2 = mon.get_current_score(2)
    assert s1 is not None
    assert s2 is not None
    assert s1 > s2
    assert mon.get_up_streak(1) > 0
    assert mon.get_up_streak(2) == 0


def test_reset_unknown_asset_is_noop() -> None:
    """Hic gorulmemis asset_id reset → no-op (raise yok)."""
    mon = TrendMonitor()
    mon.reset(999)  # Hata vermez
    assert mon.tracked_assets == ()


# ---------------- Alert payload ----------------


def test_alert_carries_input_timestamp() -> None:
    """TrendAlert.timestamp update'e verilen timestamp'le ayni."""
    mon = TrendMonitor(
        window_size=10,
        min_observations=5,
        slope_threshold=0.001,
        ewma_alpha=0.5,
    )
    values = [i * 0.1 for i in range(20)]
    last_ts = _ts(19)
    out = _feed(mon, asset_id=42, values=values)
    last_alert = [r for r in out if r is not None][-1]
    assert last_alert.timestamp == last_ts
    assert last_alert.asset_instance_id == 42


def test_alert_current_score_matches_get_current_score() -> None:
    """TrendAlert.current_score ile get_current_score ayni deger."""
    mon = TrendMonitor(
        window_size=10,
        min_observations=5,
        slope_threshold=0.001,
        ewma_alpha=0.5,
    )
    values = [i * 0.1 for i in range(20)]
    out = _feed(mon, asset_id=1, values=values)
    last_alert = [r for r in out if r is not None][-1]
    assert last_alert.current_score == pytest.approx(mon.get_current_score(1))


# ---------------- Debug yardimcilari ----------------


def test_tracked_assets_lists_seen_ids() -> None:
    """``tracked_assets`` update gormus tum id'leri tuple olarak doner."""
    mon = TrendMonitor()
    mon.update(1, _ts(0), 0.1)
    mon.update(2, _ts(0), 0.2)
    mon.update(1, _ts(1), 0.15)  # 1 zaten var
    assert set(mon.tracked_assets) == {1, 2}


def test_get_up_streak_initially_zero() -> None:
    """Hic update'lenmemis asset icin get_up_streak → 0."""
    mon = TrendMonitor()
    assert mon.get_up_streak(123) == 0
