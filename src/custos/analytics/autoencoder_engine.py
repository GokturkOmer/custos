"""sklearn MLPRegressor tabanli autoencoder anomaly engine.

Wind pivot Faz 1.3 (2026-05-12). Fraunhofer CARE paper baseline'inda
autoencoder skoru 0.66, Isolation Forest skoru 0.50; %24 performans
artisi icin IF ile yan yana kosturulur.

Wind pivot Faz 2 Prompt 5 (2026-05-12) ile genisletildi: ``alpha``
(L2 regularization), ``activation`` (relu/tanh/logistic) ve
``n_iter_no_change`` (early stopping patience) parametreleri CLI'dan
ayarlanabilir hale getirildi. Hyperparameter grid search ile AE CARE
skorunu 0.502'den 0.60+ uzerine cikarmak hedefleniyor.

CLAUDE.md "ML kurallari" 2026-05-12 revize: sklearn MLPRegressor
autoencoder sigi NN olarak izinli (PyTorch reddedildi — 800 MB ekstra
dependency yasak).

Mimari (paper baseline ile uyumlu):
- 86 input → 32 → 8 (bottleneck) → 32 → 86 output (varsayilan)
- ReLU aktivasyon (varsayilan), Adam optimizer
- z-score scaling (per-feature mean/std, eğitim setinden hesaplanir)

Anomaly tespiti:
- Reconstruction error per row = RMSE(X_row - X_row_pred)
- Threshold = np.quantile(train_errors, 0.99) — top %1 anomaly
- is_anomaly[i] = score[i] > threshold

Eğitim kurallari (paper kurali):
- Sadece status_type ∈ {0=Normal, 2=Idling} satirlar.
- Diger durumlar (1=Derated, 3=Service, 4=Downtime, 5=Other) anomaly
  davranisi gosterebilir — egitim setine karistirilmaz.
- Test seti ASLA egitim setine karistirilmaz (CLAUDE.md kurali).

Model file naming:
- Wind: ``data/models/autoencoder_<instance_id>_wind.joblib``
- AVM IF: ``data/models/anomaly_<instance_id>.joblib`` (cakisma yok).
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import TYPE_CHECKING

import joblib
import numpy as np

if TYPE_CHECKING:
    from numpy.typing import NDArray

# Egitim sirasinda izin verilen status_type_id'ler (Fraunhofer CARE
# semantik'i). 0=Normal calisma, 2=Idling (rüzgâr yok ama makine saglikli).
# Hata davranisini ogrenmemek icin diger durumlar haricte tutulur.
DEFAULT_VALID_STATUS_TYPES: tuple[int, ...] = (0, 2)

# Reconstruction error threshold yuzdeligi (üst kuyruk = anomaly).
# 0.99 ile training set'in %1'i FP olarak isaretlenir; saha kalibrasyonunda
# revize edilebilir.
DEFAULT_THRESHOLD_QUANTILE = 0.99

# z-score scaling icin sayisal stabilite epsilon'u — std=0 olan sabit
# kolonlarda 0'a bolme hatasini engeller.
_SCALE_EPS = 1e-9

# Minimum egitim satiri — bundan az veriyle anlamli MLPRegressor egitilemez.
MIN_TRAINING_ROWS = 30

# Wind pivot Faz 2 Prompt 5 (2026-05-12) — hyperparameter default'lari.
# sklearn MLPRegressor varsayilanlariyla baslangic; grid search ile en iyi
# kombinasyon belirlenecek.
DEFAULT_ALPHA = 0.0001  # L2 regularization (sklearn default)
DEFAULT_ACTIVATION = "relu"  # 'relu' | 'tanh' | 'logistic'
DEFAULT_N_ITER_NO_CHANGE = 25  # Early stopping patience (sklearn default 10)

# Hyperparameter grid search'inde izin verilen activation'lar.
ALLOWED_ACTIVATIONS: frozenset[str] = frozenset({"relu", "tanh", "logistic", "identity"})


@dataclasses.dataclass
class AutoencoderState:
    """Joblib dump/load icin saklanan icsel state.

    MLPRegressor + scaling parametreleri + threshold + metadata. Tum
    bunlar bir arada olmali ki inference sirasinda predict (model,
    scaler) + is_anomaly (threshold) tutarli kalsin.
    """

    # sklearn modeli (predict + score_samples sahibi)
    model: object
    # Per-feature z-score scaler parametreleri (egitim setinden)
    feature_mean: NDArray[np.float64]
    feature_std: NDArray[np.float64]
    # Anomaly threshold = quantile(train_errors, train_error_quantile)
    threshold: float
    train_error_quantile: float
    # Meta (debug + sanity check icin)
    n_features: int
    n_train_samples: int


class AutoencoderAnomalyEngine:
    """sklearn MLPRegressor'i autoencoder olarak kullanan anomaly engine.

    Tipik kullanim::

        engine = AutoencoderAnomalyEngine()
        engine.train(X_train, status_types=st)  # filter applied
        scores = engine.score(X_test)
        flags = engine.is_anomaly(X_test)
        engine.save(Path("data/models/autoencoder_1_wind.joblib"))

    Egitilmemis bir engine'de ``score``/``is_anomaly``/``save`` cagrisi
    ``RuntimeError`` firlatir; bu fail-loud davranisi yanlislikla bos
    model'in production'a sizmasini engeller.
    """

    def __init__(
        self,
        *,
        hidden_layer_sizes: tuple[int, ...] = (32, 8, 32),
        random_state: int = 42,
        max_iter: int = 200,
        early_stopping: bool = True,
        valid_status_types: tuple[int, ...] = DEFAULT_VALID_STATUS_TYPES,
        threshold_quantile: float = DEFAULT_THRESHOLD_QUANTILE,
        alpha: float = DEFAULT_ALPHA,
        activation: str = DEFAULT_ACTIVATION,
        n_iter_no_change: int = DEFAULT_N_ITER_NO_CHANGE,
    ) -> None:
        """Hyperparametre konfigurasyonu.

        - ``hidden_layer_sizes``: Bottleneck dahil hidden katmanlar.
          Default (32, 8, 32) paper mimarisi ile uyumlu. Faz 2 P5 grid
          search'te (32, 16, 32) ve (64, 16, 64) da denenir.
        - ``random_state``: Reproducibility — model + np.random_state.
        - ``max_iter`` + ``early_stopping``: MLPRegressor erken durdurma.
        - ``valid_status_types``: Egitimde tutulacak status_type_id'ler.
        - ``threshold_quantile``: Anomaly cutoff (0-1).
        - ``alpha``: L2 regularization (Faz 2 P5). sklearn MLPRegressor
          default 0.0001; daha yuksek (0.001, 0.01) overfitting'i azaltabilir.
        - ``activation``: Hidden katman aktivasyon ('relu' | 'tanh' |
          'logistic' | 'identity'). Tanh seasonal pattern icin daha uygun
          olabilir (paper baseline tipik olarak relu kullanir).
        - ``n_iter_no_change``: Early stopping patience (Faz 2 P5).
          sklearn default 10; 25 ile daha uzun convergence izni veriyoruz.
        """
        if activation not in ALLOWED_ACTIVATIONS:
            msg = (
                f"activation gecersiz: {activation!r}. "
                f"Izin verilenler: {sorted(ALLOWED_ACTIVATIONS)}"
            )
            raise ValueError(msg)
        self.hidden_layer_sizes = hidden_layer_sizes
        self.random_state = random_state
        self.max_iter = max_iter
        self.early_stopping = early_stopping
        self.valid_status_types = valid_status_types
        self.threshold_quantile = threshold_quantile
        self.alpha = alpha
        self.activation = activation
        self.n_iter_no_change = n_iter_no_change
        self._state: AutoencoderState | None = None

    # --- Public properties ---

    @property
    def is_trained(self) -> bool:
        """Egitildikten sonra ``True``; ``score``/``is_anomaly`` ondan sonra cagrilir."""
        return self._state is not None

    @property
    def threshold(self) -> float:
        """Egitim sonrasi belirlenen anomaly cutoff."""
        self._require_trained()
        assert self._state is not None
        return self._state.threshold

    @property
    def n_features(self) -> int:
        """Egitim setinde gorulen feature sayisi (dimension check icin)."""
        self._require_trained()
        assert self._state is not None
        return self._state.n_features

    @property
    def n_train_samples(self) -> int:
        """Egitim setine giren satir sayisi (status filtresi sonrasi)."""
        self._require_trained()
        assert self._state is not None
        return self._state.n_train_samples

    # --- Training ---

    def train(
        self,
        samples: NDArray[np.float64],
        status_types: NDArray[np.int_] | None = None,
    ) -> None:
        """``samples`` uzerinde autoencoder egitir.

        - ``samples``: shape (n_samples, n_features). NaN/inf YASAK —
          caller temizler (impute veya drop). MLPRegressor NaN kabul etmez.
        - ``status_types``: shape (n_samples,) int array veya ``None``.
          ``None`` ise tum satirlar kullanilir; aksi halde sadece
          ``valid_status_types``a uyan satirlar.

        Eğitim asamalari:
          1. Status filtreleme (paper kurali).
          2. z-score scaling (mean/std egitim setinden).
          3. MLPRegressor.fit(samples_scaled, samples_scaled) — autoencoder.
          4. Reconstruction error hesabi → threshold.

        Yetersiz veri (``< MIN_TRAINING_ROWS``) → ``ValueError``.
        """
        # Lazy import — modul yuklenirken sklearn pahali, sadece train'de gerek.
        from sklearn.neural_network import MLPRegressor  # noqa: PLC0415

        if samples.ndim != 2:
            msg = f"samples 2D olmali, geldi: shape={samples.shape}"
            raise ValueError(msg)

        if status_types is not None:
            if status_types.shape != (samples.shape[0],):
                msg = (
                    f"status_types shape uyusmuyor: samples={samples.shape[0]}, "
                    f"status={status_types.shape}"
                )
                raise ValueError(msg)
            mask = np.isin(status_types, np.asarray(self.valid_status_types))
            samples_filtered = samples[mask]
        else:
            samples_filtered = samples

        if samples_filtered.shape[0] < MIN_TRAINING_ROWS:
            msg = (
                f"Yetersiz egitim verisi: filtreden sonra "
                f"{samples_filtered.shape[0]} satir kaldi "
                f"(min {MIN_TRAINING_ROWS})"
            )
            raise ValueError(msg)

        # z-score scaling — egitim setinin mean/std'sini sakla
        feature_mean = samples_filtered.mean(axis=0)
        feature_std = samples_filtered.std(axis=0) + _SCALE_EPS
        samples_scaled = (samples_filtered - feature_mean) / feature_std

        # MLPRegressor — autoencoder modu: target = input.
        # Faz 2 P5: alpha (L2), activation, n_iter_no_change parametre olarak
        # gecirilir; grid search'te bu eksende sweep yapilir.
        model = MLPRegressor(
            hidden_layer_sizes=self.hidden_layer_sizes,
            activation=self.activation,
            solver="adam",
            alpha=self.alpha,
            random_state=self.random_state,
            max_iter=self.max_iter,
            early_stopping=self.early_stopping,
            n_iter_no_change=self.n_iter_no_change,
        )
        model.fit(samples_scaled, samples_scaled)

        # Reconstruction error → threshold
        samples_pred = model.predict(samples_scaled)
        train_errors = _rmse_per_row(samples_scaled, samples_pred)
        threshold = float(np.quantile(train_errors, self.threshold_quantile))

        self._state = AutoencoderState(
            model=model,
            feature_mean=feature_mean,
            feature_std=feature_std,
            threshold=threshold,
            train_error_quantile=self.threshold_quantile,
            n_features=int(samples.shape[1]),
            n_train_samples=int(samples_filtered.shape[0]),
        )

    # --- Inference ---

    def score(self, samples: NDArray[np.float64]) -> NDArray[np.float64]:
        """``samples`` icin reconstruction error (per row, RMSE) doner.

        ``samples.shape[1]`` egitimdeki feature sayisiyla esit olmali.
        """
        self._require_trained()
        assert self._state is not None

        if samples.ndim != 2:
            msg = f"samples 2D olmali, geldi: shape={samples.shape}"
            raise ValueError(msg)
        if samples.shape[1] != self._state.n_features:
            msg = (
                f"Feature sayisi uyumsuz: samples={samples.shape[1]}, "
                f"egitim={self._state.n_features}"
            )
            raise ValueError(msg)

        samples_scaled = (samples - self._state.feature_mean) / self._state.feature_std
        samples_pred = self._state.model.predict(samples_scaled)  # type: ignore[attr-defined]
        return _rmse_per_row(samples_scaled, samples_pred)

    def is_anomaly(self, samples: NDArray[np.float64]) -> NDArray[np.bool_]:
        """Her satir icin bool array (``score > threshold``)."""
        self._require_trained()
        assert self._state is not None
        scores = self.score(samples)
        return scores > self._state.threshold

    # --- Persistence ---

    def save(self, path: Path) -> None:
        """Engine state'ini joblib ile diske yazar.

        Parent dizini yoksa olusturur. Egitilmemis engine kayit edilemez.
        """
        self._require_trained()
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(self._serialize(), path)

    @classmethod
    def load(cls, path: Path) -> AutoencoderAnomalyEngine:
        """Joblib dosyasindan engine'i yukler.

        Bozuk veya eski format dosya → ValueError (caller fallback'e
        gecebilir; sessizce yutmuyoruz). v1 ve v2 formatlari yukler;
        v1'de olmayan alanlar (alpha, activation, n_iter_no_change)
        modul default'larina geri duser — eski modeller skor uretmeye
        devam edebilir.
        """
        payload = joblib.load(path)
        if not isinstance(payload, dict) or payload.get("kind") not in {
            "autoencoder_v1",
            "autoencoder_v2",
        }:
            msg = (
                f"Bozuk veya uyumsuz autoencoder dosyasi: {path} "
                f"(beklenen kind='autoencoder_v1' veya 'autoencoder_v2')"
            )
            raise ValueError(msg)
        # v2 eklentilerini opsiyonel olarak oku — v1 dosyalar default'a duser.
        alpha = float(payload.get("alpha", DEFAULT_ALPHA))
        activation = str(payload.get("activation", DEFAULT_ACTIVATION))
        n_iter_no_change = int(
            payload.get("n_iter_no_change", DEFAULT_N_ITER_NO_CHANGE),
        )
        engine = cls(
            hidden_layer_sizes=tuple(payload["hidden_layer_sizes"]),
            random_state=int(payload["random_state"]),
            max_iter=int(payload["max_iter"]),
            early_stopping=bool(payload["early_stopping"]),
            valid_status_types=tuple(payload["valid_status_types"]),
            threshold_quantile=float(payload["threshold_quantile"]),
            alpha=alpha,
            activation=activation,
            n_iter_no_change=n_iter_no_change,
        )
        state_dict = payload["state"]
        engine._state = AutoencoderState(
            model=state_dict["model"],
            feature_mean=np.asarray(state_dict["feature_mean"], dtype=np.float64),
            feature_std=np.asarray(state_dict["feature_std"], dtype=np.float64),
            threshold=float(state_dict["threshold"]),
            train_error_quantile=float(state_dict["train_error_quantile"]),
            n_features=int(state_dict["n_features"]),
            n_train_samples=int(state_dict["n_train_samples"]),
        )
        return engine

    # --- Internal ---

    def _serialize(self) -> dict[str, object]:
        """save() helper'i — joblib'a verilebilen dict.

        Faz 2 P5 ile ``kind='autoencoder_v2'``; v1 dosyalar yine load
        edilebilir (yeni alanlar opsiyonel, default'a duser).
        """
        assert self._state is not None
        return {
            "kind": "autoencoder_v2",
            "hidden_layer_sizes": list(self.hidden_layer_sizes),
            "random_state": self.random_state,
            "max_iter": self.max_iter,
            "early_stopping": self.early_stopping,
            "valid_status_types": list(self.valid_status_types),
            "threshold_quantile": self.threshold_quantile,
            "alpha": self.alpha,
            "activation": self.activation,
            "n_iter_no_change": self.n_iter_no_change,
            "state": {
                "model": self._state.model,
                "feature_mean": self._state.feature_mean,
                "feature_std": self._state.feature_std,
                "threshold": self._state.threshold,
                "train_error_quantile": self._state.train_error_quantile,
                "n_features": self._state.n_features,
                "n_train_samples": self._state.n_train_samples,
            },
        }

    def _require_trained(self) -> None:
        """``train()`` cagrilmadiysa RuntimeError firlat."""
        if self._state is None:
            msg = "Engine egitilmemis; once train(X, ...) cagir."
            raise RuntimeError(msg)


def _rmse_per_row(
    a: NDArray[np.float64],
    b: NDArray[np.float64],
) -> NDArray[np.float64]:
    """Iki matriks arasi per-row RMSE; shape (n_samples,)."""
    diff = a - b
    result: NDArray[np.float64] = np.sqrt(np.mean(diff * diff, axis=1))
    return result


__all__ = [
    "ALLOWED_ACTIVATIONS",
    "DEFAULT_ACTIVATION",
    "DEFAULT_ALPHA",
    "DEFAULT_N_ITER_NO_CHANGE",
    "DEFAULT_THRESHOLD_QUANTILE",
    "DEFAULT_VALID_STATUS_TYPES",
    "MIN_TRAINING_ROWS",
    "AutoencoderAnomalyEngine",
    "AutoencoderState",
]
