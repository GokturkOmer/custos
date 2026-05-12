"""Trend Monitor — EWMA-tabanli slope monitor (Faz 2 P0 — Wind pivot).

Wind pivot Faz 2 P0 (2026-05-12). Anomaly score'larinin EWMA'lanmis
slope'unu (egim) izleyerek **yavas yukari tirmanis** (gradual increase)
tipindeki mekanik bearing arizalarini yakalar. Threshold cross (spike)
modelinden farkli: ani sicrama degil, yavas crawl-up.

Hoinka/EnBW prensibi
--------------------
"Diagnostician'lar trend arar, spike degil." Faz 1.5 kapanis sonuclari
(IF CARE 0.530, erken-tespit yalnizca 2/12 = %17) mekanik bearing
arizalarini cogunlukla fault penceresine girdikten sonra yakaladigimizi
gosterdi. Trend Monitor bu acigi kapatmak icin haftalar/gunler suren
yavas drift'i takip eder.

Algoritma
---------
1. Her tick'te raw ``anomaly_score`` gelir → EWMA(``ewma_alpha``) ile
   smoothed_score uretilir.
2. ``slope = (ewma[now] - ewma[now - window_size]) / window_size``
   (birim/tick — pozitif yukari trend, negatif asagi).
3. ``slope > slope_threshold`` ise **TrendAlert**. Negatif slope sessiz.
4. ``duration_min``: slope esiginin uzerinde **ardisik** kac tick devam
   ediyor (``tick_minutes``  ile dakikaya cevirilir).
5. Severity: ``slope > slope_threshold * severity_crit_multiplier`` →
   ``'crit'``; aksi halde ``'warn'``.

Spike filtresi
--------------
Tek bir nokta sicramasi (spike) EWMA'yi cok az hareket ettirir
(alpha=0.05 → 5% katki). Window-size lag'da slope esigi asilmaz → alert
yok. Bu spike'lari sessiz dusurur, sadece **birikimli** drift'i yakalar.

State per-asset in-memory; ``reset(asset_id)`` ile temizlenir (örn. yeni
dataset replay basinda). Warmup window (``min_observations``) doluncaya
kadar alert uretilmez — ilk birkac ornek noise olabilir.

Ölcek notu
----------
``slope_threshold=0.001`` Fraunhofer CARE AE skoru (~0-1 normalize RMSE)
icin makul. Daha buyuk olceklerde (IF score_samples'in [-0.5, +0.5])
caller pre-scale yapmali veya threshold ayarlanmali. Ayar gerekli
hyperparametre — pilot saha verisinde kalibrasyon onerilir.
"""

from __future__ import annotations

import dataclasses
from collections import deque
from datetime import datetime
from typing import Final

# 144 * 10dk = 24 saat (Fraunhofer CARE aggregate periyoduyla uyumlu)
DEFAULT_WINDOW_SIZE: Final[int] = 144
# 0.05 — yavas tepki; son ~60 ornek %95 katkida bulunur (alpha=2/(N+1) yaklasik)
DEFAULT_EWMA_ALPHA: Final[float] = 0.05
# birim/tick — AE RMSE 0-1 normalize icin makul; pilot saha kalibrasyonu icin
# parametre olarak verilmis durumda
DEFAULT_SLOPE_THRESHOLD: Final[float] = 0.001
# 72 tick = 12 saat warmup — bundan az ornek varsa alert yok
DEFAULT_MIN_OBSERVATIONS: Final[int] = 72
# Slope > threshold * 3 → 'crit'; aksi halde 'warn'
DEFAULT_SEVERITY_CRIT_MULTIPLIER: Final[float] = 3.0
# 10dk tick varsayilir (Fraunhofer CARE aggregate periyodu)
DEFAULT_TICK_MINUTES: Final[int] = 10


@dataclasses.dataclass(frozen=True)
class TrendAlert:
    """TrendMonitor'un urettigi alert ozet kaydi (immutable).

    Caller (anomaly_detector + validate script) bu kaydi DB'ye yazabilir
    veya log'a aktarabilir. ``duration_min`` slope esiginin uzerinde
    ardisik kac dakika devam ettigini gosterir — uzun sure → yuksek
    guven.
    """

    asset_instance_id: int
    timestamp: datetime
    current_score: float       # EWMA-smoothed current value
    ewma_slope: float          # birim/tick (pozitif yukari)
    duration_min: int          # tick_minutes * up_streak
    severity: str              # 'warn' veya 'crit'


@dataclasses.dataclass
class _AssetState:
    """Per-asset rolling state (private, in-memory).

    ``ewma_history`` slope hesabi icin window_size+1 uzunluklu lag
    halkasi. Maxlen ile en eski deger otomatik dusurulur.
    """

    ewma_history: deque[float]
    timestamps: deque[datetime]
    ewma_value: float | None = None
    up_streak: int = 0           # esik ustu ardisik tick sayisi


class TrendMonitor:
    """EWMA-tabanli yavas trend (slope) monitor.

    Tipik kullanim::

        mon = TrendMonitor()  # default 24sa pencere, 12sa warmup
        for tick in stream:
            alert = mon.update(asset_id, tick.timestamp, tick.score)
            if alert is not None:
                logger.warn("Trend alert", **dataclasses.asdict(alert))

    Constructor parametre dogrulamasi: gecersiz deger → ``ValueError``.
    """

    def __init__(
        self,
        *,
        window_size: int = DEFAULT_WINDOW_SIZE,
        ewma_alpha: float = DEFAULT_EWMA_ALPHA,
        slope_threshold: float = DEFAULT_SLOPE_THRESHOLD,
        min_observations: int = DEFAULT_MIN_OBSERVATIONS,
        severity_crit_multiplier: float = DEFAULT_SEVERITY_CRIT_MULTIPLIER,
        tick_minutes: int = DEFAULT_TICK_MINUTES,
    ) -> None:
        """Hyperparametre konfigurasyonu.

        * ``window_size``: Slope hesabinda lag (tick). Default 144 = 24sa.
        * ``ewma_alpha``: EWMA smoothing factor (0, 1]. Default 0.05.
        * ``slope_threshold``: Birim/tick. Default 0.001 (AE RMSE 0-1 olcegi).
        * ``min_observations``: Warmup (tick). Default 72 = 12sa.
        * ``severity_crit_multiplier``: Crit esigi = slope_threshold * bu kat.
        * ``tick_minutes``: Bir tick'in dakika cinsinden suresi (duration
          hesabinda kullanilir).
        """
        if window_size < 2:
            msg = f"window_size en az 2 olmali, geldi: {window_size}"
            raise ValueError(msg)
        if not (0.0 < ewma_alpha <= 1.0):
            msg = f"ewma_alpha (0, 1] araliginda olmali, geldi: {ewma_alpha}"
            raise ValueError(msg)
        if slope_threshold < 0.0:
            msg = f"slope_threshold negatif olamaz, geldi: {slope_threshold}"
            raise ValueError(msg)
        if min_observations < 2:
            msg = (
                f"min_observations en az 2 olmali, geldi: {min_observations}"
            )
            raise ValueError(msg)
        if min_observations > window_size:
            # Bu durumda warmup tamamlandiginda lag = window_size dolmus olur
            # ama min_observations > window_size olunca slope hesabi mantiksiz
            # bir lag esit min_observations gibi davranirdi; tutarlilik icin
            # erken hata.
            msg = (
                f"min_observations ({min_observations}) > window_size "
                f"({window_size}): warmup tutarsiz"
            )
            raise ValueError(msg)
        if severity_crit_multiplier < 1.0:
            msg = (
                f"severity_crit_multiplier en az 1.0 olmali, geldi: "
                f"{severity_crit_multiplier}"
            )
            raise ValueError(msg)
        if tick_minutes < 1:
            msg = f"tick_minutes pozitif olmali, geldi: {tick_minutes}"
            raise ValueError(msg)
        self.window_size = window_size
        self.ewma_alpha = ewma_alpha
        self.slope_threshold = slope_threshold
        self.min_observations = min_observations
        self.severity_crit_multiplier = severity_crit_multiplier
        self.tick_minutes = tick_minutes
        self._states: dict[int, _AssetState] = {}

    def update(
        self,
        asset_instance_id: int,
        timestamp: datetime,
        anomaly_score: float,
    ) -> TrendAlert | None:
        """Tek bir tick raw score'unu isle; alert varsa donder.

        Algoritma:
        1. EWMA guncelle (ilk ornek -> raw score).
        2. History buffer'a smoothed degeri yaz.
        3. Warmup kontrolu (``min_observations``).
        4. Slope = (ewma[now] - ewma[now - lag]) / lag.
        5. Esik asimi → up_streak++, alert ureyt; aksi halde streak=0.

        Hiçbir alert üretilmiyorsa ``None`` doner. Negatif slope (asagi
        trend) sessiz — sadece pozitif yukari kayma alarm üretir.
        """
        state = self._states.get(asset_instance_id)
        if state is None:
            state = _AssetState(
                # +1: slope hesabi icin window_size oncesine erisim gerek
                ewma_history=deque(maxlen=self.window_size + 1),
                timestamps=deque(maxlen=self.window_size + 1),
            )
            self._states[asset_instance_id] = state

        # 1. EWMA guncelle
        if state.ewma_value is None:
            state.ewma_value = anomaly_score
        else:
            state.ewma_value = (
                self.ewma_alpha * anomaly_score
                + (1.0 - self.ewma_alpha) * state.ewma_value
            )
        state.ewma_history.append(state.ewma_value)
        state.timestamps.append(timestamp)

        # 2. Warmup
        if len(state.ewma_history) < self.min_observations:
            state.up_streak = 0
            return None

        # 3. Slope hesabi — lag = min(window_size, len-1)
        lag = min(self.window_size, len(state.ewma_history) - 1)
        if lag < 1:
            return None  # defansif (warmup zaten kontrol ediyor)
        slope = (
            state.ewma_history[-1] - state.ewma_history[-1 - lag]
        ) / lag

        # 4. Streak guncelle
        if slope > self.slope_threshold:
            state.up_streak += 1
        else:
            state.up_streak = 0
            return None

        # 5. Alert üret
        severity = (
            "crit"
            if slope > self.slope_threshold * self.severity_crit_multiplier
            else "warn"
        )
        return TrendAlert(
            asset_instance_id=asset_instance_id,
            timestamp=timestamp,
            current_score=float(state.ewma_value),
            ewma_slope=float(slope),
            duration_min=state.up_streak * self.tick_minutes,
            severity=severity,
        )

    def reset(self, asset_instance_id: int) -> None:
        """Bir asset'in state'ini sifirlar (yeni dataset baslangici).

        Asset hic gorulmediyse no-op.
        """
        self._states.pop(asset_instance_id, None)

    def reset_all(self) -> None:
        """Tum asset state'lerini sifirlar (testlerde + restart pattern'inde)."""
        self._states.clear()

    def get_current_score(self, asset_instance_id: int) -> float | None:
        """Son EWMA-smoothed score (test/debug); state yoksa None."""
        state = self._states.get(asset_instance_id)
        if state is None:
            return None
        return state.ewma_value

    def get_up_streak(self, asset_instance_id: int) -> int:
        """Mevcut up-streak tick sayisi (test/debug); state yoksa 0."""
        state = self._states.get(asset_instance_id)
        if state is None:
            return 0
        return state.up_streak

    @property
    def tracked_assets(self) -> tuple[int, ...]:
        """Aktif takip edilen asset_instance_id'leri (immutable kopya)."""
        return tuple(self._states.keys())


__all__ = [
    "DEFAULT_EWMA_ALPHA",
    "DEFAULT_MIN_OBSERVATIONS",
    "DEFAULT_SEVERITY_CRIT_MULTIPLIER",
    "DEFAULT_SLOPE_THRESHOLD",
    "DEFAULT_TICK_MINUTES",
    "DEFAULT_WINDOW_SIZE",
    "TrendAlert",
    "TrendMonitor",
]
