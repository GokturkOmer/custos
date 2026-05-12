"""Fraunhofer CARE benchmark scorer (Coverage + Accuracy + Reliability + Earliness).

Wind pivot Faz 1.4 (2026-05-12). Custos anomaly detection (IF + AE) ciktilarini
akademik bir time-series anomaly tespit standardiyla karsilastirmak icin
implement edilmistir.

Referans: Hoinka et al., "CARE - A Domain-Specific Evaluation Metric for
Time Series Anomaly Detection", arXiv:2404.10320v2 (2024).

Tasarim ozeti
-------------
Skor 4 alt-bilesenin agirlikli ortalamasi:

* **Coverage**: Anomaly olaylarinda detection F_beta'sinin (anomaly event'i
  basina) ortalamasi. False positive 4 kat daha agir cezalandirilir
  (beta=0.5). Status_type filtresi (sadece 0=Normal, 2=Idling) opsiyonel.
* **Accuracy**: Normal davranis dataset'lerinde yanlis alarm direnci —
  tn / (fp + tn). Bunun ortalamasi.
* **Reliability**: Kritiklik sayaci ile event-level F_beta. ``tc`` esiginden
  fazla ardisik pred=1 → event detected. Olay-bazinda confusion matrix.
* **Earliness**: Anomaly event icinde tahminin ne kadar erken geldigi.
  Parcali agirlik fonksiyonu: ilk yarida w=1.0 (tam kredi); ikinci yarida
  w = 2*(1 - position) lineer azalma.

Final::

    WA = (w_cov * Cov + w_earl * Earl + w_rel * Rel + w_acc * Acc) / sum(w)

Ozel durumlar (paper Eq. 5):
* Hicbir tahmin pozitif degilse (preds.sum() == 0) → CARE = 0
* Acc < 0.5 ise → CARE = Acc (zayif normal davranis tanima fallback'i)
* Aksi halde → CARE = WA

Kullanim
--------
::

    from custos.analytics.care_scorer import CAREScorer, Event

    events = [
        Event(event_id=0, label="anomaly", event_start_id=100, event_end_id=200),
        Event(event_id=1, label="normal", event_start_id=201, event_end_id=400),
    ]
    scorer = CAREScorer()
    result = scorer.score(predictions=preds, ground_truth_events=events)
    # result.final → CARE skoru
    # result.sub_scores → dict (coverage / accuracy / reliability / earliness)
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from numpy.typing import NDArray


# Default agirliklar (coverage, earliness, reliability, accuracy).
# Paper: w_acc=2 (normal davranis tanima anomaly tanimasiyla dengelensin).
DEFAULT_WEIGHTS: tuple[float, float, float, float] = (1.0, 1.0, 1.0, 2.0)

# Default beta — false positive cezasi (1/beta^2) kat. 0.5 → FP cezasi 4x.
DEFAULT_BETA = 0.5

# Default kritiklik esigi (timestep). 72 = 12 saat (10 dk aggregate'te).
DEFAULT_TC = 72

# Paper Table 3.3: 0=Normal, 2=Idling saglikli; 1/3/4/5 anormal status,
# evaluation'dan disarida tutulur.
DEFAULT_VALID_STATUS_TYPES: tuple[int, ...] = (0, 2)

# Accuracy fallback esigi — Acc bunun altina duserse final = Acc.
ACC_FALLBACK_THRESHOLD = 0.5

# Earliness piecewise breakpoint — bu pozisyonun altinda w=1, ustunde lineer azalma.
EARLINESS_FULL_CREDIT_LIMIT = 0.5


@dataclasses.dataclass(frozen=True)
class Event:
    """Tek bir CARE event/dataset metadatasi (event_info.csv satirina denk).

    Indeksler, ``CAREScorer.score()``'a verilen ``predictions`` array'inde
    kapsayici (inclusive) [start, end] araligini gosterir.

    * **Anomaly event**: ``[event_start_id, event_end_id]`` = fault window
      (GT=1 burada). ``dataset_start_id`` / ``dataset_end_id`` verilirse,
      fault window'unu cevreleyen pre/post lead-in dahil edilir (FP'ler
      bu lead-in'lerden gelir). Verilmezse fault = dataset window kabul
      edilir (basit kullanim).
    * **Normal event**: fault yok; ``[event_start_id, event_end_id]``
      dataset window'unu temsil eder. Tum slice GT=0 sayilir.
    """

    event_id: int
    label: str
    event_start_id: int
    event_end_id: int
    dataset_start_id: int | None = None
    dataset_end_id: int | None = None

    def __post_init__(self) -> None:
        """Field dogrulama — gec hata vermek yerine kuruluş sirasinda yakala."""
        if self.label not in {"anomaly", "normal"}:
            msg = (
                f"label 'anomaly' veya 'normal' olmali, geldi: {self.label!r}"
            )
            raise ValueError(msg)
        if self.event_start_id < 0 or self.event_end_id < 0:
            msg = (
                f"event_start_id / event_end_id negatif olamaz: "
                f"start={self.event_start_id}, end={self.event_end_id}"
            )
            raise ValueError(msg)
        if self.event_end_id < self.event_start_id:
            msg = (
                f"event_end_id < event_start_id: "
                f"{self.event_end_id} < {self.event_start_id}"
            )
            raise ValueError(msg)
        if self.dataset_start_id is not None and self.dataset_end_id is not None:
            if self.dataset_end_id < self.dataset_start_id:
                msg = (
                    f"dataset_end_id < dataset_start_id: "
                    f"{self.dataset_end_id} < {self.dataset_start_id}"
                )
                raise ValueError(msg)
            if (
                self.dataset_start_id > self.event_start_id
                or self.dataset_end_id < self.event_end_id
            ):
                msg = (
                    "dataset window event window'u kapsamiyor: "
                    f"dataset=[{self.dataset_start_id},{self.dataset_end_id}], "
                    f"event=[{self.event_start_id},{self.event_end_id}]"
                )
                raise ValueError(msg)

    @property
    def ds_start(self) -> int:
        """Dataset window start; verilmediyse fault window start'a esit."""
        if self.dataset_start_id is None:
            return self.event_start_id
        return self.dataset_start_id

    @property
    def ds_end(self) -> int:
        """Dataset window end; verilmediyse fault window end'e esit."""
        if self.dataset_end_id is None:
            return self.event_end_id
        return self.dataset_end_id


@dataclasses.dataclass(frozen=True)
class CAREResult:
    """CARE hesabinin sonucu.

    ``final`` ana skordur; ``sub_scores`` debug ve raporlama icin ham
    bilesen + meta verileri tasir.
    """

    coverage: float
    accuracy: float
    reliability: float
    earliness: float
    final: float
    sub_scores: dict[str, float]


class CAREScorer:
    """Fraunhofer CARE benchmark scorer (paper Eq. 5).

    Tipik kullanim::

        scorer = CAREScorer()  # default tc=72, beta=0.5, weights=(1,1,1,2)
        result = scorer.score(predictions, ground_truth_events)
        # result.final → CARE skoru (0.0 - 1.0)

    Constructor parametreleri sirasinda hatali deger gelirse ``ValueError``
    firlatir; bu erken-kontrol yanlislikla 0 agirlikla calismanin önüne gecer.
    """

    def __init__(
        self,
        *,
        tc: int = DEFAULT_TC,
        beta: float = DEFAULT_BETA,
        weights: tuple[float, float, float, float] = DEFAULT_WEIGHTS,
        valid_status_types: tuple[int, ...] = DEFAULT_VALID_STATUS_TYPES,
    ) -> None:
        """Hyperparametre konfigurasyonu.

        * ``tc``: Kritiklik sayaci esigi (timestep). Default 72 = 12 saat
          (10 dk aggregate). Sayac bu degere ulasirsa event detected.
        * ``beta``: F_beta beta parametresi. Default 0.5 → FP cezasi 4x.
        * ``weights``: ``(w_coverage, w_earliness, w_reliability, w_accuracy)``.
          Paper default (1, 1, 1, 2) — normal tanimanin agirligi 2 kat.
        * ``valid_status_types``: Coverage + Accuracy'de geçerli sayilan
          status_type_id'ler. Paper Table 3.3: 0=Normal, 2=Idling. Status
          arr verilmezse filtre devre disi.
        """
        if tc < 1:
            msg = f"tc en az 1 olmali, geldi: {tc}"
            raise ValueError(msg)
        if beta <= 0:
            msg = f"beta pozitif olmali, geldi: {beta}"
            raise ValueError(msg)
        if len(weights) != 4 or any(w < 0 for w in weights):
            msg = (
                f"weights 4 negatif olmayan float olmali, geldi: {weights}"
            )
            raise ValueError(msg)
        if sum(weights) <= 0:
            msg = "weights toplami pozitif olmali"
            raise ValueError(msg)
        self.tc = tc
        self.beta = beta
        self.weights = weights
        self.valid_status_types = valid_status_types

    def score(
        self,
        predictions: NDArray[np.int_] | NDArray[np.bool_],
        ground_truth_events: list[Event],
        timestamps: NDArray[np.datetime64] | None = None,
        *,
        status_types: NDArray[np.int_] | None = None,
    ) -> CAREResult:
        """Tum predictions + event metadata uzerinden CARE skoru hesaplar.

        * ``predictions``: 1D 0/1 array (n_timesteps,). Bool veya int kabul.
        * ``ground_truth_events``: ``event_info.csv``'ten dervire Event listesi.
          Bos liste izinli ama anlamsiz (coverage/accuracy=0).
        * ``timestamps``: Opsiyonel, shape (n_timesteps,). Su anda
          sadece shape sanity check icin kullanilir; hesaba girmez.
        * ``status_types``: Opsiyonel, shape (n_timesteps,) status_type_id.
          Verilirse Coverage + Accuracy bunlara gore filtrelenir
          (sadece ``valid_status_types``'taki satirlar dahil edilir).
        """
        if predictions.ndim != 1:
            msg = f"predictions 1D olmali, geldi: shape={predictions.shape}"
            raise ValueError(msg)
        if status_types is not None and status_types.shape != predictions.shape:
            msg = (
                f"status_types shape uyumsuz: preds={predictions.shape}, "
                f"status={status_types.shape}"
            )
            raise ValueError(msg)
        if timestamps is not None and timestamps.shape != predictions.shape:
            msg = (
                f"timestamps shape uyumsuz: preds={predictions.shape}, "
                f"timestamps={timestamps.shape}"
            )
            raise ValueError(msg)

        n = predictions.shape[0]
        for ev in ground_truth_events:
            if ev.ds_end >= n or ev.ds_start < 0:
                msg = (
                    f"Event {ev.event_id} indeksleri predictions sinirlari "
                    f"disinda: ds=[{ev.ds_start},{ev.ds_end}], n={n}"
                )
                raise ValueError(msg)

        # Bool / int normalize
        preds_bin: NDArray[np.int_] = (np.asarray(predictions) != 0).astype(np.int_)

        anomaly_events = [e for e in ground_truth_events if e.label == "anomaly"]
        normal_events = [e for e in ground_truth_events if e.label == "normal"]

        coverage = self._coverage(preds_bin, anomaly_events, status_types)
        accuracy = self._accuracy(preds_bin, normal_events, status_types)
        reliability = self._reliability(preds_bin, ground_truth_events)
        earliness = self._earliness(preds_bin, anomaly_events)

        w_cov, w_earl, w_rel, w_acc = self.weights
        w_sum = sum(self.weights)
        wa = (
            w_cov * coverage
            + w_earl * earliness
            + w_rel * reliability
            + w_acc * accuracy
        ) / w_sum

        # Ozel durumlar (paper Eq. 5)
        if int(preds_bin.sum()) == 0:
            # Hicbir anomaly tahmin edilmedi → CARE 0
            final = 0.0
        elif accuracy < ACC_FALLBACK_THRESHOLD:
            # Zayif normal davranis tanima → CARE = Acc
            final = accuracy
        else:
            final = wa

        sub_scores: dict[str, float] = {
            "coverage": coverage,
            "accuracy": accuracy,
            "reliability": reliability,
            "earliness": earliness,
            "weighted_average": wa,
            "n_anomaly_events": float(len(anomaly_events)),
            "n_normal_events": float(len(normal_events)),
            "n_positive_predictions": float(int(preds_bin.sum())),
        }

        return CAREResult(
            coverage=coverage,
            accuracy=accuracy,
            reliability=reliability,
            earliness=earliness,
            final=final,
            sub_scores=sub_scores,
        )

    # --- Alt skor hesaplari ---

    def _coverage(
        self,
        preds_bin: NDArray[np.int_],
        anomaly_events: list[Event],
        status_types: NDArray[np.int_] | None,
    ) -> float:
        """Anomaly event'leri uzerinde F_beta'nin ortalamasi.

        Bos liste → 0.0 (paper'da boyle bir durumda Coverage sub-score'u
        anlamsiz ama final WA hesabinda 0 olarak alinir).
        """
        scores: list[float] = []
        for ev in anomaly_events:
            f_beta = self._event_f_beta(preds_bin, ev, status_types)
            if f_beta is not None:
                scores.append(f_beta)
        if not scores:
            return 0.0
        return float(np.mean(scores))

    def _accuracy(
        self,
        preds_bin: NDArray[np.int_],
        normal_events: list[Event],
        status_types: NDArray[np.int_] | None,
    ) -> float:
        """Normal event'leri uzerinde tn/(fp+tn) ortalamasi.

        Bos liste → 0.0 (yine WA hesabinda 0 olarak alinir).
        """
        scores: list[float] = []
        for ev in normal_events:
            acc = self._event_accuracy(preds_bin, ev, status_types)
            if acc is not None:
                scores.append(acc)
        if not scores:
            return 0.0
        return float(np.mean(scores))

    def _reliability(
        self,
        preds_bin: NDArray[np.int_],
        events: list[Event],
    ) -> float:
        """Event-level F_beta: kritiklik sayaci >= tc → detected; sonra
        F_beta(detected, label=='anomaly').
        """
        if not events:
            return 0.0

        tp = fp = fn = tn = 0
        for ev in events:
            ds_slice: NDArray[np.int_] = preds_bin[ev.ds_start : ev.ds_end + 1]
            detected = _criticality_max(ds_slice) >= self.tc
            is_anomaly = ev.label == "anomaly"
            if is_anomaly and detected:
                tp += 1
            elif is_anomaly and not detected:
                fn += 1
            elif not is_anomaly and detected:
                fp += 1
            else:
                tn += 1
        f_beta = _f_beta(tp, fp, fn, self.beta)
        return 0.0 if f_beta is None else f_beta

    def _earliness(
        self,
        preds_bin: NDArray[np.int_],
        anomaly_events: list[Event],
    ) -> float:
        """Anomaly event'leri uzerinde weighted-earliness ortalamasi."""
        scores: list[float] = []
        for ev in anomaly_events:
            es = self._event_earliness(preds_bin, ev)
            if es is not None:
                scores.append(es)
        if not scores:
            return 0.0
        return float(np.mean(scores))

    # --- Per-event yardimcilari ---

    def _event_f_beta(
        self,
        preds_bin: NDArray[np.int_],
        event: Event,
        status_types: NDArray[np.int_] | None,
    ) -> float | None:
        """Bir anomaly event'inde F_beta hesaplar.

        GT semantigi:
        * ``[event_start_id, event_end_id]`` araligi → GT=1 (fault window)
        * Dataset window'unun fault disindaki bolumu → GT=0 (lead-in)

        Status filtresi varsa ``valid_status_types`` disindaki satirlar
        atlanir. tp+fp+fn=0 → None (skip).
        """
        idx = np.arange(event.ds_start, event.ds_end + 1)
        if status_types is not None:
            mask = np.isin(
                status_types[idx], np.asarray(self.valid_status_types)
            )
            idx = idx[mask]
        if idx.size == 0:
            return None

        preds_slice = preds_bin[idx]
        gt = (
            (idx >= event.event_start_id) & (idx <= event.event_end_id)
        ).astype(np.int_)

        tp = int(np.sum((preds_slice == 1) & (gt == 1)))
        fp = int(np.sum((preds_slice == 1) & (gt == 0)))
        fn = int(np.sum((preds_slice == 0) & (gt == 1)))
        return _f_beta(tp, fp, fn, self.beta)

    def _event_accuracy(
        self,
        preds_bin: NDArray[np.int_],
        event: Event,
        status_types: NDArray[np.int_] | None,
    ) -> float | None:
        """Bir normal event'inde Acc = tn/(fp+tn) hesaplar.

        Tum slice GT=0 oldugu icin tp/fn yoktur. Filter sonrasi bos →
        None (skip).
        """
        idx = np.arange(event.ds_start, event.ds_end + 1)
        if status_types is not None:
            mask = np.isin(
                status_types[idx], np.asarray(self.valid_status_types)
            )
            idx = idx[mask]
        if idx.size == 0:
            return None
        preds_slice = preds_bin[idx]
        fp = int(np.sum(preds_slice == 1))
        tn = int(np.sum(preds_slice == 0))
        if fp + tn == 0:
            return None
        return tn / (fp + tn)

    def _event_earliness(
        self,
        preds_bin: NDArray[np.int_],
        event: Event,
    ) -> float | None:
        """Bir anomaly event'inde weighted earliness score'u doner.

        Position: ``[event_start_id, event_end_id]`` araligini [0, 1]'e
        normalize eder. Parcali agirlik:
            * pos < 0.5 → w = 1.0 (tam kredi)
            * pos >= 0.5 → w = 2*(1 - pos) (lineer azalma 1 → 0)

        WS = sum(w * pred) / sum(w). Fault window 0 uzunlukta → None.
        """
        s, e = event.event_start_id, event.event_end_id
        n = e - s + 1
        if n <= 0:
            return None
        if n == 1:
            # Tek timestep'lik fault — pos=0 sayalim, tam kredi.
            return float(preds_bin[s])

        positions = np.arange(n, dtype=np.float64) / (n - 1)
        weights_arr = np.where(
            positions < EARLINESS_FULL_CREDIT_LIMIT,
            1.0,
            2.0 * (1.0 - positions),
        )
        # Numeric safety — formul zaten 0'in altina dusmuyor ama yine de clamp.
        weights_arr = np.maximum(weights_arr, 0.0)
        denom = float(weights_arr.sum())
        if denom == 0.0:
            return 0.0
        return float(
            np.sum(weights_arr * preds_bin[s : e + 1].astype(np.float64)) / denom
        )


# --- Modul seviyesi yardimcilar ---


def _f_beta(tp: int, fp: int, fn: int, beta: float) -> float | None:
    """F_beta = (1+b^2)*tp / ((1+b^2)*tp + b^2*fn + fp).

    * tp=fp=fn=0 → None (event'te higbir positive yok; skip et).
    * tp=0 ama fp+fn>0 → 0.0 (model bir sey kacirdi/yanlis tahmin etti).
    """
    if tp == 0 and fp == 0 and fn == 0:
        return None
    beta_sq = beta * beta
    num = (1.0 + beta_sq) * tp
    denom = (1.0 + beta_sq) * tp + beta_sq * fn + fp
    if denom == 0.0:
        return 0.0
    return num / denom


def _criticality_max(preds: NDArray[np.int_]) -> int:
    """Kritiklik sayacinin max degeri.

    Algoritma:
        counter = 0
        her timestep:
            pred=1 ise counter += 1
            pred=0 ise counter = max(counter - 1, 0)
        return max(counter)

    Bu pattern, "tc kadar ardisik pozitif (kucuk kesintilere tolerans)"
    semantigini saglar. Saf "longest run of 1s"den farkli — tek bir 0
    sayaci sifirlamiyor, sadece 1 azaltiyor.
    """
    counter = 0
    max_c = 0
    for p in preds.tolist():
        if p:
            counter += 1
        else:
            counter = counter - 1 if counter > 0 else 0
        if counter > max_c:
            max_c = counter
    return max_c


__all__ = [
    "ACC_FALLBACK_THRESHOLD",
    "DEFAULT_BETA",
    "DEFAULT_TC",
    "DEFAULT_VALID_STATUS_TYPES",
    "DEFAULT_WEIGHTS",
    "EARLINESS_FULL_CREDIT_LIMIT",
    "CAREResult",
    "CAREScorer",
    "Event",
]
