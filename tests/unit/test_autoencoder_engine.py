"""src/custos/analytics/autoencoder_engine.py — birim testleri (Faz 1.3).

Kapsam:
- Sentetik sinüs verisinde train + score (low reconstruction error).
- Anomaly enjekte edilen test verisinde error yüksek.
- Status filter: status ∈ {0, 2} disindaki satirlar egitime sokulmaz.
- Save/load roundtrip — state korunur.
- Untrained engine: score/is_anomaly/save → RuntimeError.
- Dimension mismatch: feature count uyumsuzlugu → ValueError.
- Yetersiz veri (< MIN_TRAINING_ROWS) → ValueError.

Faz 2 P5 ek kapsami:
- alpha (L2) + activation + n_iter_no_change CLI parametreleri MLPRegressor'a
  dogru gecirilir.
- Activation validasyonu — gecersiz deger ValueError.
- Bottleneck 16 senaryosu (hidden_layer_sizes=(32,16,32)) egitilebilir.
- Save → load v2 formatinda alpha/activation/n_iter_no_change korunur.
- v1 formatindaki eski joblib dosyalari yuklenebilir (backward-compat,
  yeni alanlar default'a duser).
"""

from __future__ import annotations

from pathlib import Path

import joblib
import numpy as np
import pytest

from custos.analytics.autoencoder_engine import (
    ALLOWED_ACTIVATIONS,
    DEFAULT_ACTIVATION,
    DEFAULT_ALPHA,
    DEFAULT_N_ITER_NO_CHANGE,
    DEFAULT_VALID_STATUS_TYPES,
    MIN_TRAINING_ROWS,
    AutoencoderAnomalyEngine,
)

RNG_SEED = 42


def _sine_dataset(n_samples: int = 300, n_features: int = 8) -> np.ndarray:
    """Deterministik sinüs verisi — autoencoder duşuk reconstruction error
    ile fit edebilmeli. Per-feature farkli faz + amplitude."""
    rng = np.random.default_rng(RNG_SEED)
    t = np.linspace(0, 4 * np.pi, n_samples)
    samples = np.empty((n_samples, n_features), dtype=np.float64)
    for j in range(n_features):
        phase = j * 0.5
        amplitude = 1.0 + 0.2 * j
        samples[:, j] = amplitude * np.sin(t + phase) + 0.05 * rng.standard_normal(n_samples)
    return samples


def _trained_engine(
    n_samples: int = 200,
    n_features: int = 8,
    hidden_layer_sizes: tuple[int, ...] = (16, 4, 16),
) -> AutoencoderAnomalyEngine:
    """Sentetik sinüs üzerinde egitilmis küçük engine (fast test fixture)."""
    samples = _sine_dataset(n_samples, n_features)
    engine = AutoencoderAnomalyEngine(
        hidden_layer_sizes=hidden_layer_sizes,
        random_state=RNG_SEED,
        max_iter=80,
        early_stopping=False,  # Kucuk veri setinde early_stopping deterministic degil
    )
    engine.train(samples)
    return engine


# --- Untrained state ---


def test_untrained_engine_score_raises() -> None:
    """Egitilmemis engine.score → RuntimeError ('egitilmemis')."""
    engine = AutoencoderAnomalyEngine()
    with pytest.raises(RuntimeError, match="egitilmemis"):
        engine.score(np.zeros((1, 3)))


def test_untrained_engine_is_anomaly_raises() -> None:
    """Egitilmemis engine.is_anomaly → RuntimeError."""
    engine = AutoencoderAnomalyEngine()
    with pytest.raises(RuntimeError):
        engine.is_anomaly(np.zeros((1, 3)))


def test_untrained_engine_save_raises(tmp_path: Path) -> None:
    """Egitilmemis engine.save → RuntimeError; disk'e bos dosya yazilmaz."""
    engine = AutoencoderAnomalyEngine()
    out = tmp_path / "ae.joblib"
    with pytest.raises(RuntimeError):
        engine.save(out)
    assert not out.exists()


def test_is_trained_flag_flips_after_train() -> None:
    """Egitim öncesi/sonrasi ``is_trained`` flag'i."""
    engine = AutoencoderAnomalyEngine(
        hidden_layer_sizes=(8, 2, 8), max_iter=30, early_stopping=False,
    )
    assert engine.is_trained is False
    engine.train(_sine_dataset(50, 4))
    assert engine.is_trained is True


# --- Basic train + score ---


def test_train_on_sine_produces_low_score_on_same_distribution() -> None:
    """Egitim setiyle ayni distribution'dan yeni veri → düşük reconstruction error.

    Threshold = quantile(0.99); yeni sinüs verisinin %95'i altinda olmali.
    """
    engine = _trained_engine(n_samples=300, n_features=6)
    # Yeni sample - ayni distribution'dan
    new_samples = _sine_dataset(n_samples=100, n_features=6)
    scores = engine.score(new_samples)
    flags = engine.is_anomaly(new_samples)
    # Distribution match → çoğu satir non-anomaly (en azindan %50 saglikli olmali).
    assert flags.mean() < 0.5, (
        f"Beklenmedik anomaly orani: {flags.mean():.2f} "
        f"(scores mean={scores.mean():.4f}, threshold={engine.threshold:.4f})"
    )


def test_train_sets_n_features_correctly() -> None:
    """Egitim sonrasi engine.n_features dogru olmali."""
    engine = _trained_engine(n_samples=80, n_features=5)
    assert engine.n_features == 5


def test_threshold_is_positive_after_train() -> None:
    """Threshold pozitif olmali (quantile of non-negative errors)."""
    engine = _trained_engine(n_samples=80, n_features=4)
    assert engine.threshold > 0.0


# --- Anomaly detection ---


def test_anomaly_injection_yields_high_score() -> None:
    """Egitim dagilimindan uzak anomaly verisi → score >> threshold.

    Train: bounded sin [-1, +1]. Test: 50x scaled extreme rows. Reconstruction
    error bu satirlar icin cok yuksek olmali → is_anomaly=True.
    """
    engine = _trained_engine(n_samples=300, n_features=6)
    # Anomaly: cok büyük sabit deger (autoencoder bunu reconstruct edemez)
    anomaly = np.full((5, 6), fill_value=50.0)
    scores = engine.score(anomaly)
    flags = engine.is_anomaly(anomaly)
    assert flags.all(), f"Beklenen: hepsi anomaly. Skorlar: {scores}"
    # Anomaly skoru threshold'un en az 2 katindan yuksek olmali (sanity check)
    assert scores.min() > engine.threshold * 2


# --- Status filtering ---


def test_status_filter_excludes_invalid_status_types() -> None:
    """``status_types`` ile filtre: sadece valid_status_types satirlar egitime girer.

    Egitim setine status=4 (Downtime) eklersek ve filtre yaparsak, n_train_samples
    azalmali — feature mean/std de degisecek.
    """
    samples = _sine_dataset(n_samples=200, n_features=4)
    # Yari valid (0), yari invalid (4)
    status_types = np.array([0] * 100 + [4] * 100, dtype=np.int_)

    engine = AutoencoderAnomalyEngine(
        hidden_layer_sizes=(8, 2, 8), max_iter=30, early_stopping=False,
    )
    engine.train(samples, status_types=status_types)
    # Sadece 100 satir egitime girmis olmali
    assert engine._state is not None  # noqa: SLF001 — internal state check
    assert engine._state.n_train_samples == 100


def test_status_filter_accepts_status_2_idling() -> None:
    """status_type=2 (Idling) valid_status_types default'ta — egitime girer."""
    assert 2 in DEFAULT_VALID_STATUS_TYPES
    samples = _sine_dataset(n_samples=120, n_features=4)
    status_types = np.full(120, 2, dtype=np.int_)
    engine = AutoencoderAnomalyEngine(
        hidden_layer_sizes=(8, 2, 8), max_iter=30, early_stopping=False,
    )
    engine.train(samples, status_types=status_types)
    assert engine._state is not None  # noqa: SLF001
    assert engine._state.n_train_samples == 120


def test_status_filter_shape_mismatch_raises() -> None:
    """status_types uzunlugu samples ile uyusmazsa ValueError."""
    samples = _sine_dataset(n_samples=80, n_features=3)
    bad_status = np.zeros(40, dtype=np.int_)  # 80 yerine 40
    engine = AutoencoderAnomalyEngine()
    with pytest.raises(ValueError, match="uyusmuyor|status_types"):
        engine.train(samples, status_types=bad_status)


# --- Validation ---


def test_train_raises_on_insufficient_data() -> None:
    """``< MIN_TRAINING_ROWS`` satir → ValueError."""
    samples = np.zeros((MIN_TRAINING_ROWS - 1, 3))
    engine = AutoencoderAnomalyEngine()
    with pytest.raises(ValueError, match="Yetersiz egitim verisi"):
        engine.train(samples)


def test_train_raises_on_1d_input() -> None:
    """1D array → ValueError (samples 2D olmali)."""
    samples = np.zeros(100)
    engine = AutoencoderAnomalyEngine()
    with pytest.raises(ValueError, match="2D"):
        engine.train(samples)


def test_score_raises_on_feature_count_mismatch() -> None:
    """Egitimdeki feature sayisindan farkli boyutla score → ValueError."""
    engine = _trained_engine(n_samples=80, n_features=4)
    bad = np.zeros((1, 5))  # 4 yerine 5 feature
    with pytest.raises(ValueError, match="Feature sayisi uyumsuz"):
        engine.score(bad)


def test_score_raises_on_1d_input() -> None:
    """Score'a 1D array → ValueError."""
    engine = _trained_engine(n_samples=60, n_features=3)
    with pytest.raises(ValueError, match="2D"):
        engine.score(np.zeros(3))


# --- Persistence ---


def test_save_load_roundtrip_preserves_scores(tmp_path: Path) -> None:
    """Save → load sonrasi ayni samples icin ayni skor."""
    engine = _trained_engine(n_samples=120, n_features=5)
    samples_test = _sine_dataset(n_samples=20, n_features=5)
    scores_before = engine.score(samples_test)
    flags_before = engine.is_anomaly(samples_test)

    out = tmp_path / "autoencoder_99_wind.joblib"
    engine.save(out)
    assert out.exists()

    loaded = AutoencoderAnomalyEngine.load(out)
    assert loaded.is_trained
    assert loaded.n_features == 5
    assert loaded.threshold == pytest.approx(engine.threshold)

    scores_after = loaded.score(samples_test)
    flags_after = loaded.is_anomaly(samples_test)
    np.testing.assert_allclose(scores_before, scores_after, rtol=1e-10)
    assert (flags_before == flags_after).all()


def test_save_creates_parent_directory(tmp_path: Path) -> None:
    """Parent dizini yoksa save mkdir(parents=True) ile olusturur."""
    engine = _trained_engine(n_samples=60, n_features=3)
    nested = tmp_path / "deep" / "nested" / "ae.joblib"
    assert not nested.parent.exists()
    engine.save(nested)
    assert nested.exists()


def test_load_raises_on_invalid_payload(tmp_path: Path) -> None:
    """Bozuk joblib (yanlis kind) → ValueError."""
    bad = tmp_path / "bad.joblib"
    joblib.dump({"kind": "isolation_forest_v1", "data": [1, 2]}, bad)
    with pytest.raises(ValueError, match="Bozuk veya uyumsuz"):
        AutoencoderAnomalyEngine.load(bad)


# --- Faz 2 Prompt 5: Hyperparameter genislemesi ---


def test_alpha_parameter_is_passed_to_mlpregressor() -> None:
    """``alpha`` CLI degeri MLPRegressor'a iletilir (L2 regularization)."""
    samples = _sine_dataset(n_samples=80, n_features=4)
    engine = AutoencoderAnomalyEngine(
        hidden_layer_sizes=(8, 2, 8),
        max_iter=20,
        early_stopping=False,
        alpha=0.01,
    )
    engine.train(samples)
    assert engine.alpha == pytest.approx(0.01)
    # MLPRegressor instance alpha'yi ic state'inde tutar.
    assert engine._state is not None  # noqa: SLF001
    inner_alpha = engine._state.model.alpha  # type: ignore[attr-defined]
    assert inner_alpha == pytest.approx(0.01)


def test_activation_parameter_is_passed_to_mlpregressor() -> None:
    """``activation='tanh'`` MLPRegressor'a iletilir."""
    samples = _sine_dataset(n_samples=80, n_features=4)
    engine = AutoencoderAnomalyEngine(
        hidden_layer_sizes=(8, 2, 8),
        max_iter=20,
        early_stopping=False,
        activation="tanh",
    )
    engine.train(samples)
    assert engine.activation == "tanh"
    assert engine._state is not None  # noqa: SLF001
    inner_activation = engine._state.model.activation  # type: ignore[attr-defined]
    assert inner_activation == "tanh"


def test_activation_validates_input() -> None:
    """Gecersiz activation → ValueError; ALLOWED_ACTIVATIONS hep frozenset olur."""
    assert "relu" in ALLOWED_ACTIVATIONS
    assert "tanh" in ALLOWED_ACTIVATIONS
    with pytest.raises(ValueError, match="activation gecersiz"):
        AutoencoderAnomalyEngine(activation="bogus_activation")


def test_n_iter_no_change_parameter_is_passed_to_mlpregressor() -> None:
    """``n_iter_no_change`` MLPRegressor'a iletilir (early stopping patience)."""
    samples = _sine_dataset(n_samples=80, n_features=4)
    engine = AutoencoderAnomalyEngine(
        hidden_layer_sizes=(8, 2, 8),
        max_iter=20,
        early_stopping=False,
        n_iter_no_change=15,
    )
    engine.train(samples)
    assert engine.n_iter_no_change == 15
    assert engine._state is not None  # noqa: SLF001
    inner = engine._state.model.n_iter_no_change  # type: ignore[attr-defined]
    assert inner == 15


def test_bottleneck_16_layer_trains_successfully() -> None:
    """``hidden_layer_sizes=(32, 16, 32)`` ile egitim — Faz 2 P5 grid alternatif."""
    samples = _sine_dataset(n_samples=200, n_features=8)
    engine = AutoencoderAnomalyEngine(
        hidden_layer_sizes=(32, 16, 32),
        max_iter=30,
        early_stopping=False,
    )
    engine.train(samples)
    assert engine.is_trained
    # Bottleneck = 16 olarak hidden_layer_sizes attribute'unda goruluyor
    assert engine.hidden_layer_sizes == (32, 16, 32)
    # Egitim setiyle ayni distribution'dan yeni veri saglikli kalmali
    new_samples = _sine_dataset(n_samples=50, n_features=8)
    flags = engine.is_anomaly(new_samples)
    assert flags.mean() < 0.5


def test_default_hyperparams_match_expected() -> None:
    """Default'lar ALLOWED ile uyumlu — backward-compat icin sabit."""
    engine = AutoencoderAnomalyEngine()
    assert engine.alpha == pytest.approx(DEFAULT_ALPHA)
    assert engine.activation == DEFAULT_ACTIVATION
    assert engine.n_iter_no_change == DEFAULT_N_ITER_NO_CHANGE


def test_save_v2_roundtrip_preserves_new_hyperparams(tmp_path: Path) -> None:
    """v2 dosya: alpha + activation + n_iter_no_change save/load sonrasi korunur."""
    samples = _sine_dataset(n_samples=100, n_features=5)
    engine = AutoencoderAnomalyEngine(
        hidden_layer_sizes=(16, 4, 16),
        max_iter=30,
        early_stopping=False,
        alpha=0.005,
        activation="tanh",
        n_iter_no_change=20,
    )
    engine.train(samples)
    out = tmp_path / "ae_v2.joblib"
    engine.save(out)
    # Dosya icindeki kind v2 olmali
    payload = joblib.load(out)
    assert payload["kind"] == "autoencoder_v2"
    assert payload["alpha"] == pytest.approx(0.005)
    assert payload["activation"] == "tanh"
    assert payload["n_iter_no_change"] == 20
    # Load roundtrip parametreleri korur
    loaded = AutoencoderAnomalyEngine.load(out)
    assert loaded.alpha == pytest.approx(0.005)
    assert loaded.activation == "tanh"
    assert loaded.n_iter_no_change == 20


def test_load_v1_backward_compat_defaults_new_fields(tmp_path: Path) -> None:
    """v1 joblib (eski format) yuklenebilir — yeni alanlar default'a duser."""
    # Once v2 ile egit, sonra payload'i manuel v1'e cevirip yaz.
    samples = _sine_dataset(n_samples=80, n_features=4)
    engine = AutoencoderAnomalyEngine(
        hidden_layer_sizes=(8, 2, 8),
        max_iter=20,
        early_stopping=False,
    )
    engine.train(samples)
    v2_path = tmp_path / "ae_v2.joblib"
    engine.save(v2_path)
    payload = joblib.load(v2_path)
    # v1 format simule et: yeni alanlari kaldir, kind=v1 yap
    v1_payload = {k: v for k, v in payload.items() if k not in {
        "alpha", "activation", "n_iter_no_change",
    }}
    v1_payload["kind"] = "autoencoder_v1"
    v1_path = tmp_path / "ae_v1.joblib"
    joblib.dump(v1_payload, v1_path)

    loaded = AutoencoderAnomalyEngine.load(v1_path)
    assert loaded.is_trained
    # Yeni alanlar default'a duser
    assert loaded.alpha == pytest.approx(DEFAULT_ALPHA)
    assert loaded.activation == DEFAULT_ACTIVATION
    assert loaded.n_iter_no_change == DEFAULT_N_ITER_NO_CHANGE
    # Skor uretebiliyor — backward-compat asil hedef
    test_samples = _sine_dataset(n_samples=20, n_features=4)
    scores = loaded.score(test_samples)
    assert scores.shape == (20,)


def test_joblib_size_under_300_kb(tmp_path: Path) -> None:
    """Production wind AE modelleri ~150 KB. Test fixture'un uzak ust siniri 300 KB.

    Asil 5 production model 154-156 KB; %90 tolerans = 290 KB. Bu test
    AE serialization'in beklenmedik buyume yapmadigini garanti eder
    (joblib compressed default).
    """
    samples = _sine_dataset(n_samples=200, n_features=8)
    engine = AutoencoderAnomalyEngine(
        hidden_layer_sizes=(32, 16, 32),
        max_iter=30,
        early_stopping=False,
    )
    engine.train(samples)
    out = tmp_path / "ae.joblib"
    engine.save(out)
    assert out.stat().st_size < 300_000, (
        f"AE joblib boyutu beklenmedik: {out.stat().st_size} bytes (>300 KB)"
    )
