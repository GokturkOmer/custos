"""src/custos/analytics/care_scorer.py — birim testleri (Faz 1.4).

Kapsam:
* Validation (kurucu + score() argumanlari)
* Event __post_init__ guard'lari
* Special durumlar (all-zero, all-one, no positive preds)
* Coverage / Accuracy / Reliability / Earliness ozellikle dogrulama:
    - Mukemmel tahmin → CARE = 1.0
    - All-anomaly → CARE = 0 (Acc<0.5 fallback)
    - All-normal → CARE = 0 (no positive)
    - Random 50/50 → CARE ~0.5 (paper baseline'i)
* Erken vs gec alarm: ayni recall, farkli earliness
* Reliability: 11 ardisik alarm tc=12 altinda → TP degil
* F_beta helper: tp=fp=fn=0 → None
* Kritiklik sayaci semantigi (kesintili 1'lere tolerans)
"""

from __future__ import annotations

import numpy as np
import pytest

from custos.analytics.care_scorer import (
    ACC_FALLBACK_THRESHOLD,
    DEFAULT_TC,
    CAREResult,
    CAREScorer,
    Event,
    _criticality_max,
    _f_beta,
)

# --- Event __post_init__ ---


def test_event_label_validation() -> None:
    """Gecersiz label → ValueError."""
    with pytest.raises(ValueError, match="label"):
        Event(event_id=0, label="bilinmiyor", event_start_id=0, event_end_id=10)


def test_event_negative_indices_rejected() -> None:
    """Negatif indeks → ValueError."""
    with pytest.raises(ValueError, match="negatif"):
        Event(event_id=0, label="anomaly", event_start_id=-1, event_end_id=10)


def test_event_end_before_start_rejected() -> None:
    """event_end_id < event_start_id → ValueError."""
    with pytest.raises(ValueError, match="event_end_id"):
        Event(event_id=0, label="anomaly", event_start_id=10, event_end_id=5)


def test_event_dataset_must_cover_fault_window() -> None:
    """dataset window fault window'u kapsamiyorsa → ValueError."""
    with pytest.raises(ValueError, match="kapsamiyor"):
        Event(
            event_id=0,
            label="anomaly",
            event_start_id=50,
            event_end_id=100,
            dataset_start_id=60,  # fault'tan sonra basliyor
            dataset_end_id=120,
        )


def test_event_dataset_end_before_dataset_start_rejected() -> None:
    """dataset_end < dataset_start → ValueError."""
    with pytest.raises(ValueError, match="dataset_end_id"):
        Event(
            event_id=0,
            label="anomaly",
            event_start_id=50,
            event_end_id=100,
            dataset_start_id=200,
            dataset_end_id=150,
        )


def test_event_ds_start_end_default_to_event_window() -> None:
    """dataset_*_id verilmediyse ds_start/ds_end fault window'a esit."""
    ev = Event(event_id=0, label="anomaly", event_start_id=50, event_end_id=100)
    assert ev.ds_start == 50
    assert ev.ds_end == 100


# --- CAREScorer kurucu ---


def test_scorer_invalid_tc_raises() -> None:
    """tc < 1 → ValueError."""
    with pytest.raises(ValueError, match="tc"):
        CAREScorer(tc=0)


def test_scorer_invalid_beta_raises() -> None:
    """beta <= 0 → ValueError."""
    with pytest.raises(ValueError, match="beta"):
        CAREScorer(beta=0.0)


def test_scorer_invalid_weights_length_raises() -> None:
    """weights uzunlugu 4 degil → ValueError."""
    with pytest.raises(ValueError, match="weights"):
        CAREScorer(weights=(1.0, 1.0, 1.0))  # type: ignore[arg-type]


def test_scorer_negative_weights_rejected() -> None:
    """Negatif agirlik → ValueError."""
    with pytest.raises(ValueError, match="weights"):
        CAREScorer(weights=(-1.0, 1.0, 1.0, 1.0))


def test_scorer_zero_total_weight_rejected() -> None:
    """Toplam agirlik 0 → ValueError (sifira bolme onleme)."""
    with pytest.raises(ValueError, match="toplami"):
        CAREScorer(weights=(0.0, 0.0, 0.0, 0.0))


# --- score() validation ---


def test_score_2d_predictions_raises() -> None:
    """predictions 1D olmali."""
    scorer = CAREScorer()
    preds = np.zeros((10, 2), dtype=np.int_)
    with pytest.raises(ValueError, match="predictions 1D"):
        scorer.score(preds, [])


def test_score_status_shape_mismatch_raises() -> None:
    """status_types ve predictions ayni shape olmali."""
    scorer = CAREScorer()
    preds = np.zeros(10, dtype=np.int_)
    status = np.zeros(5, dtype=np.int_)
    with pytest.raises(ValueError, match="status_types shape"):
        scorer.score(preds, [], status_types=status)


def test_score_timestamps_shape_mismatch_raises() -> None:
    """timestamps ve predictions ayni shape olmali."""
    scorer = CAREScorer()
    preds = np.zeros(10, dtype=np.int_)
    ts = np.zeros(5, dtype="datetime64[s]")
    with pytest.raises(ValueError, match="timestamps shape"):
        scorer.score(preds, [], timestamps=ts)


def test_score_event_out_of_bounds_raises() -> None:
    """Event indeksleri predictions sinirlarinin disinda → ValueError."""
    scorer = CAREScorer()
    preds = np.zeros(10, dtype=np.int_)
    ev = Event(event_id=0, label="anomaly", event_start_id=5, event_end_id=20)
    with pytest.raises(ValueError, match="sinirlari disinda"):
        scorer.score(preds, [ev])


# --- _f_beta helper ---


def test_f_beta_returns_none_for_all_zero() -> None:
    """tp=fp=fn=0 → None (skip semantigi)."""
    assert _f_beta(0, 0, 0, 0.5) is None


def test_f_beta_perfect_predictions() -> None:
    """Mukemmel: tp>0, fp=fn=0 → 1.0."""
    assert _f_beta(10, 0, 0, 0.5) == pytest.approx(1.0)


def test_f_beta_all_missed() -> None:
    """tp=0, fn>0, fp=0 → 0.0 (denom>0, num=0)."""
    assert _f_beta(0, 0, 10, 0.5) == pytest.approx(0.0)


def test_f_beta_penalizes_fp_4x_for_beta_half() -> None:
    """beta=0.5 → FP cezasi FN'in 4 kati. tp=8, fp=4, fn=4 vs tp=8, fp=1, fn=16."""
    # (1+0.25)*8 / ((1.25)*8 + 0.25*4 + 4) = 10 / (10 + 1 + 4) = 10/15 = 0.667
    score_high_fp = _f_beta(8, 4, 4, 0.5)
    # (1.25)*8 / ((1.25)*8 + 0.25*16 + 1) = 10 / (10 + 4 + 1) = 10/15 = 0.667
    score_high_fn = _f_beta(8, 1, 16, 0.5)
    # 4 FP ~= 16 FN (4x ceza)
    assert score_high_fp is not None
    assert score_high_fn is not None
    assert score_high_fp == pytest.approx(score_high_fn, rel=1e-9)


# --- Kritiklik sayaci semantigi ---


def test_criticality_max_zero_input() -> None:
    """Hicbir 1 yok → max=0."""
    preds = np.zeros(50, dtype=np.int_)
    assert _criticality_max(preds) == 0


def test_criticality_max_all_ones() -> None:
    """Tum 1'ler → max = len."""
    preds = np.ones(50, dtype=np.int_)
    assert _criticality_max(preds) == 50


def test_criticality_grace_decrement() -> None:
    """Tek bir 0, sayaci sifirlamiyor — sadece 1 azaltiyor."""
    # 5 adet 1, 1 adet 0, 5 adet 1 → max = 5-1+5 = 9
    preds = np.array([1] * 5 + [0] + [1] * 5, dtype=np.int_)
    assert _criticality_max(preds) == 9


def test_criticality_eleven_ones_below_threshold() -> None:
    """11 ardisik 1, tc=12 → max=11 (TP esigine ulasmaz)."""
    preds = np.array([1] * 11 + [0] * 100, dtype=np.int_)
    assert _criticality_max(preds) == 11


# --- Ozel durum: all-zero predictions ---


def test_all_zero_predictions_yields_care_zero() -> None:
    """Hicbir tahmin yok → CARE = 0 (paper Eq. 5 ozel durum)."""
    preds = np.zeros(200, dtype=np.int_)
    events = [
        Event(event_id=0, label="anomaly", event_start_id=10, event_end_id=50),
        Event(event_id=1, label="normal", event_start_id=51, event_end_id=199),
    ]
    scorer = CAREScorer()
    result = scorer.score(preds, events)
    assert result.final == 0.0
    # Accuracy yine de 1.0 olmalı (normal event'te hiç FP yok)
    assert result.accuracy == pytest.approx(1.0)
    # Coverage 0 (hiç TP yok)
    assert result.coverage == 0.0


# --- Ozel durum: all-one predictions ---


def test_all_one_predictions_yields_care_zero_via_acc_fallback() -> None:
    """Surekli alarm → Acc<0.5 (her satır FP) → CARE = Acc (0.0)."""
    preds = np.ones(200, dtype=np.int_)
    events = [
        Event(
            event_id=0,
            label="anomaly",
            event_start_id=50,
            event_end_id=99,
            dataset_start_id=0,
            dataset_end_id=99,
        ),
        Event(event_id=1, label="normal", event_start_id=100, event_end_id=199),
    ]
    scorer = CAREScorer()
    result = scorer.score(preds, events)
    # Accuracy=0 (her satır FP), Acc<0.5 fallback → CARE=0
    assert result.accuracy == pytest.approx(0.0)
    assert result.final == pytest.approx(0.0)


# --- Mukemmel tahmin ---


def test_perfect_predictions_yields_care_one() -> None:
    """Tam isabetli model → CARE ~ 1.0."""
    n = 300
    preds = np.zeros(n, dtype=np.int_)
    # Anomaly event fault window: [50, 200] — yeterince uzun (≥ tc=72)
    preds[50:201] = 1
    events = [
        Event(
            event_id=0,
            label="anomaly",
            event_start_id=50,
            event_end_id=200,
            dataset_start_id=0,
            dataset_end_id=200,
        ),
        Event(event_id=1, label="normal", event_start_id=201, event_end_id=299),
    ]
    scorer = CAREScorer()
    result = scorer.score(preds, events)
    assert result.coverage == pytest.approx(1.0)
    assert result.accuracy == pytest.approx(1.0)
    assert result.reliability == pytest.approx(1.0)
    # Earliness < 1.0 cunku fault window'un tum bolumunde pred=1; ikinci yarida
    # weighted avg=1 cunku weights ve preds tutarli ama sum(w*pred)/sum(w) =
    # sum(w)/sum(w) = 1.0 (tum preds=1 oldugu icin).
    assert result.earliness == pytest.approx(1.0)
    assert result.final == pytest.approx(1.0)


# --- Erken vs gec alarm (ayni recall, farkli earliness) ---


def test_early_alarm_higher_earliness_than_late_alarm() -> None:
    """Ayni sayida pred=1 fault window'da; erken konumlanmis olan
    yuksek earliness."""
    n_event = 100
    # Erken: ilk yarida 10 alarm
    preds_early = np.zeros(120, dtype=np.int_)
    preds_early[10:30] = 1  # event_start=10, ilk 20 timestep
    # Gec: ikinci yarida 10 alarm
    preds_late = np.zeros(120, dtype=np.int_)
    preds_late[90:110] = 1  # event_end=109, son 20 timestep
    event = Event(
        event_id=0,
        label="anomaly",
        event_start_id=10,
        event_end_id=10 + n_event - 1,
    )

    scorer = CAREScorer()
    res_early = scorer.score(preds_early, [event])
    res_late = scorer.score(preds_late, [event])
    # Coverage benzer (ikisinde de 20 tp, 80 fn, 0 fp) → ayni F_beta
    assert res_early.coverage == pytest.approx(res_late.coverage, abs=1e-9)
    # Earliness farkli: erken yuksek, gec dusuk.
    # Beklenen degerler: sum(w)=74.75 (analytic). Erken 20*1/74.75 ≈ 0.27,
    # Gec (positions ≥0.8) toplam ~3.84/74.75 ≈ 0.05.
    assert res_early.earliness == pytest.approx(0.268, abs=0.02)
    assert res_late.earliness == pytest.approx(0.051, abs=0.02)
    # WA (Acc fallback olmadan, ham agirlikli ortalama) erken > gec olmali.
    # Final CARE her ikisinde de 0 (corpus'ta normal event yok → Acc=0 → fallback).
    assert (
        res_early.sub_scores["weighted_average"]
        > res_late.sub_scores["weighted_average"]
    )


# --- Reliability: 11 ardisik alarm tc=12 altinda ---


def test_reliability_eleven_consecutive_below_threshold() -> None:
    """11 ardisik alarm, tc=12 → event detected sayilmaz → anomaly event FN."""
    n = 200
    preds = np.zeros(n, dtype=np.int_)
    preds[10:21] = 1  # 11 ardisik 1
    events = [
        Event(
            event_id=0,
            label="anomaly",
            event_start_id=10,
            event_end_id=50,
            dataset_start_id=0,
            dataset_end_id=99,
        ),
        Event(event_id=1, label="normal", event_start_id=100, event_end_id=199),
    ]
    scorer = CAREScorer(tc=12)
    result = scorer.score(preds, events)
    # Anomaly event detected degil (max counter 11 < 12) → FN
    # Normal event detected degil (zaten 0) → TN
    # tp=0, fn=1, fp=0, tn=1 → F_β: 0/(0+0.25+0) = 0
    assert result.reliability == pytest.approx(0.0)


def test_reliability_twelve_consecutive_above_threshold() -> None:
    """12 ardisik alarm, tc=12 → event detected → anomaly event TP."""
    n = 200
    preds = np.zeros(n, dtype=np.int_)
    preds[10:22] = 1  # 12 ardisik 1
    events = [
        Event(
            event_id=0,
            label="anomaly",
            event_start_id=10,
            event_end_id=50,
            dataset_start_id=0,
            dataset_end_id=99,
        ),
        Event(event_id=1, label="normal", event_start_id=100, event_end_id=199),
    ]
    scorer = CAREScorer(tc=12)
    result = scorer.score(preds, events)
    # tp=1 (anomaly detected), fp=0, fn=0, tn=1 → F_β = 1.25/(1.25+0+0) = 1.0
    assert result.reliability == pytest.approx(1.0)


# --- Paper baseline'lari (yaklasik dogrulamali) ---


def _make_test_corpus(rng_seed: int = 42) -> tuple[
    np.random.Generator, list[Event], int, int,
]:
    """Sentetik 10 anomaly + 10 normal event'lik bir corpus.

    Her event 300 timestep. Anomaly event'inde lead-in 200, fault 100
    (event_start_id=200, event_end_id=299 her event icin). Fault window
    100 timestep > DEFAULT_TC=72 → kritiklik sayaci esik asabilir.

    Toplam: 6000 timestep, 20 event, 10 anomaly + 10 normal.
    """
    rng = np.random.default_rng(rng_seed)
    events: list[Event] = []
    n_events = 20
    event_len = 300
    fault_start_offset = 200  # ilk 200 timestep lead-in, son 100 fault
    fault_end_offset = 299    # event_len-1

    for i in range(n_events):
        start = i * event_len
        end = start + event_len - 1
        if i < 10:
            # Anomaly event
            events.append(
                Event(
                    event_id=i,
                    label="anomaly",
                    event_start_id=start + fault_start_offset,
                    event_end_id=start + fault_end_offset,
                    dataset_start_id=start,
                    dataset_end_id=end,
                ),
            )
        else:
            # Normal event
            events.append(
                Event(
                    event_id=i,
                    label="normal",
                    event_start_id=start,
                    event_end_id=end,
                ),
            )
    n_total = n_events * event_len
    # rng kullanimi callsite'da; burada sadece corpus yapisi
    _ = rng  # placeholder; testlerde kullaniliyor
    return rng, events, n_total, event_len


def test_baseline_random_close_to_half() -> None:
    """Random 50/50 baseline — paper'da CARE ≈ 0.5.

    Bizim implementasyonumuzda kesin 0.5'i degil, makul bir aralikta
    bekleriz (sentetik corpus paper'in 95 dataset'inden cok kucuk).
    """
    rng, events, n_total, _ = _make_test_corpus(rng_seed=7)
    preds = rng.integers(0, 2, size=n_total, dtype=np.int_)
    scorer = CAREScorer()
    result = scorer.score(preds, events)
    # Random: ~50% Acc, ~50% Coverage (recall-like), Earliness ortalama 0.5
    # CARE WA ~ (0.5+0.5+EF+2*0.5)/5 ~ 0.5 (EF bilesimine bagli)
    # Paper toleransi ± 0.15 (kucuk corpus + dataset farki)
    assert 0.30 < result.final < 0.70, (
        f"Random baseline {result.final:.3f} beklenenden uzak; "
        f"sub_scores={result.sub_scores}"
    )


def test_baseline_all_anomaly_yields_zero() -> None:
    """All-anomaly: tum tahminler 1. Paper'da CARE=0."""
    _, events, n_total, _ = _make_test_corpus()
    preds = np.ones(n_total, dtype=np.int_)
    scorer = CAREScorer()
    result = scorer.score(preds, events)
    # Acc=0 (normal event'lerde tum tahminler FP), Acc<0.5 → CARE = Acc = 0
    assert result.accuracy == pytest.approx(0.0)
    assert result.final == pytest.approx(0.0)


def test_baseline_all_normal_yields_zero() -> None:
    """All-normal: tum tahminler 0. Paper'da CARE=0 (no positive preds)."""
    _, events, n_total, _ = _make_test_corpus()
    preds = np.zeros(n_total, dtype=np.int_)
    scorer = CAREScorer()
    result = scorer.score(preds, events)
    # Hicbir pozitif tahmin → ozel durum → CARE=0
    assert result.final == 0.0
    # Ama Accuracy = 1.0 (mukemmel reddetme)
    assert result.accuracy == pytest.approx(1.0)


def test_baseline_ideal_oracle_yields_near_one() -> None:
    """Ideal model: anomaly fault window'larinda 1, her yerde 0. CARE ~1."""
    _, events, n_total, _ = _make_test_corpus()
    preds = np.zeros(n_total, dtype=np.int_)
    for ev in events:
        if ev.label == "anomaly":
            preds[ev.event_start_id : ev.event_end_id + 1] = 1
    scorer = CAREScorer()
    result = scorer.score(preds, events)
    # Coverage = 1, Accuracy = 1, Reliability = 1, Earliness < 1 (cunku
    # fault window'unun ikinci yarisi kabul edildi)
    assert result.coverage == pytest.approx(1.0)
    assert result.accuracy == pytest.approx(1.0)
    assert result.reliability == pytest.approx(1.0)
    # Earliness: tum fault window=1 → WS = sum(w)/sum(w) = 1
    assert result.earliness == pytest.approx(1.0)
    assert result.final > 0.95


# --- Status type filtreleme ---


def test_status_filter_excludes_invalid_ids() -> None:
    """status_types ile filter aktif → 1/3/4/5 satirlari evaluation'a girmez.

    Anomaly event'inin fault window'unun yarisinda status=3 (Service):
    filtre ile sadece status ∈ {0, 2} kalan satirlar degerlendirilir.
    """
    n = 200
    preds = np.zeros(n, dtype=np.int_)
    # Tahminler sadece fault'un ikinci yarisinda 1
    preds[80:101] = 1  # event_start=50, event_end=100
    # Fault'un ilk yarisinda status=3 (Service) → bu satirlar evaluation'dan
    # cikartilir. Filter sonrasi sadece fault'un ikinci yarisi degerlendirilir
    # → tp=20, fp=0, fn=0 → F_β=1.0
    status = np.zeros(n, dtype=np.int_)
    status[50:80] = 3  # service

    events = [
        Event(
            event_id=0,
            label="anomaly",
            event_start_id=50,
            event_end_id=100,
            dataset_start_id=0,
            dataset_end_id=199,
        ),
    ]
    scorer = CAREScorer()
    res_with_filter = scorer.score(preds, events, status_types=status)
    res_no_filter = scorer.score(preds, events)
    # Filter ile coverage daha yuksek (FN'ler maskelendi)
    assert res_with_filter.coverage > res_no_filter.coverage
    # Filter ile coverage=1.0 (kalan tum satirlar TP)
    assert res_with_filter.coverage == pytest.approx(1.0)


# --- CAREResult tipi ---


def test_result_is_dataclass_with_expected_fields() -> None:
    """CAREResult tum 4 alt-skor + final + sub_scores tasiyor."""
    preds = np.zeros(100, dtype=np.int_)
    events = [Event(event_id=0, label="normal", event_start_id=0, event_end_id=99)]
    scorer = CAREScorer()
    res: CAREResult = scorer.score(preds, events)
    assert hasattr(res, "coverage")
    assert hasattr(res, "accuracy")
    assert hasattr(res, "reliability")
    assert hasattr(res, "earliness")
    assert hasattr(res, "final")
    assert "n_anomaly_events" in res.sub_scores
    assert "n_normal_events" in res.sub_scores
    assert "weighted_average" in res.sub_scores


def test_acc_fallback_threshold_is_half() -> None:
    """ACC_FALLBACK_THRESHOLD sabiti 0.5 — paper Eq. 5."""
    assert ACC_FALLBACK_THRESHOLD == pytest.approx(0.5)


def test_default_tc_is_72() -> None:
    """DEFAULT_TC 72 timestep = 12 saat 10dk aggregate'te."""
    assert DEFAULT_TC == 72
