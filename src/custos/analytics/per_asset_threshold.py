"""Per-asset adaptive threshold calibrator (Wind pivot Faz 2 Prompt 2).

Wind pivot Faz 2 Prompt 2 (2026-05-12). Faz 1 paradoksu: ayni ariza tipinin
asset_0 ve asset_10 turbinlerinde **farkli baseline gurultu** seviyelerinde
ortaya cikmasi nedeniyle tek bir global quantile(0.99) threshold asset_0'da
1.5 gun ONCE, asset_10'da 4.5 gun GEC tetiklendi. Bu modul her asset +
engine icin ayri threshold tutar:

- Gurultulu asset'lerde threshold yukari kayar → false-positive yagmuru
  azalir (accuracy artar).
- Sessiz asset'lerde threshold asagi kayar → erken-tespit oranindan
  feragat etmeden coverage artar.

Engine konvansiyonu
-------------------
``engine_type='ae'`` (autoencoder)
    Skor = reconstruction RMSE; **ust kuyruk** anomaly. ``quantile=0.99``
    (training set'in %1'i fp olarak isaretlensin). ``is_anomaly =
    score > threshold``.

``engine_type='if'`` (Isolation Forest)
    Skor = ``IsolationForest.score_samples()`` ([-0.5, +0.5] cevresi);
    **alt kuyruk** anomaly. ``quantile=0.01`` (training set'in alt %1'i
    fp olarak isaretlensin). ``is_anomaly = score < threshold``.

Yon kontrolu calibrator'da degil caller'da: ``is_anomaly_at_threshold(
score, engine_type, threshold)`` helper'i yon konvansiyonunu uygular.

AVM dokunulmazligi
------------------
Migration 042 ``custos_wind`` disinda NO-OP — AVM'de tablo OLUSMAZ. Caller
``CUSTOS_PER_ASSET_THRESHOLD`` env'ini 'off' birakirsa calibrator
inisializasyonda hicbir DB cagrisi yapmaz; AVM yan etkisiz kalir. Env
'on' iken ve tablo eksikse ilk DB cagrisi ``UndefinedTableError`` firlatir
(caller try/except ile yumusatabilir).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Final

import numpy as np
import structlog

from custos.shared.database import (
    ASSET_THRESHOLD_ENGINE_TYPE_VALUES,
    AssetThreshold,
    DatabaseInterface,
)

logger = structlog.get_logger(logger_name="per_asset_threshold")

# Master switch env var — AVM safe-default 'off'. Wind .env'inde 'on' yapilir.
_ENABLED_ENV: Final[str] = "CUSTOS_PER_ASSET_THRESHOLD"
_ENABLED_VALUES: Final[frozenset[str]] = frozenset({"on", "1", "true", "yes"})

# Default quantile'lar — caller override edebilir. AE ust kuyruk, IF alt kuyruk.
DEFAULT_QUANTILE_AE: Final[float] = 0.99
DEFAULT_QUANTILE_IF: Final[float] = 0.01

# Kalibrasyon icin minimum training skor sayisi — bundan az olunca
# quantile gurultulu olur, fallback'e gec.
MIN_TRAINING_SCORES: Final[int] = 30


def resolve_enabled() -> bool:
    """``CUSTOS_PER_ASSET_THRESHOLD`` env var → bool (default False — AVM safe).

    Module-level helper; test fixture'lari bunu monkeypatch eder.
    """
    raw = os.environ.get(_ENABLED_ENV, "off").strip().lower()
    return raw in _ENABLED_VALUES


def default_quantile(engine_type: str) -> float:
    """Engine tipine gore makul quantile (AE=0.99, IF=0.01).

    Bilinmeyen engine_type → ``ValueError`` (fail-loud — caller'in
    yon konvansiyonunu acikca verdiginden emin olalim).
    """
    if engine_type == "ae":
        return DEFAULT_QUANTILE_AE
    if engine_type == "if":
        return DEFAULT_QUANTILE_IF
    msg = (
        f"Bilinmeyen engine_type={engine_type!r}; "
        f"izinli: {sorted(ASSET_THRESHOLD_ENGINE_TYPE_VALUES)}"
    )
    raise ValueError(msg)


def is_anomaly_at_threshold(
    score: float,
    engine_type: str,
    threshold: float,
) -> bool:
    """Yon-bilinçli karsilastirma.

    AE → ``score > threshold`` (ust kuyruk).
    IF → ``score < threshold`` (alt kuyruk).
    Bilinmeyen engine → ``ValueError``.
    """
    if engine_type == "ae":
        return score > threshold
    if engine_type == "if":
        return score < threshold
    msg = (
        f"Bilinmeyen engine_type={engine_type!r}; "
        f"izinli: {sorted(ASSET_THRESHOLD_ENGINE_TYPE_VALUES)}"
    )
    raise ValueError(msg)


class PerAssetThresholdCalibrator:
    """Asset + engine basina anomaly threshold kalibrasyonu + cache.

    Tipik kullanim (offline kalibrasyon)::

        cal = PerAssetThresholdCalibrator(db=db, enabled=True)
        await cal.warm_cache()
        await cal.calibrate(asset_id=1, engine_type='ae',
                            training_scores=train_rmse)
        t = cal.get_threshold(1, 'ae')  # cache'den senkron oku

    Tipik kullanim (online inference)::

        cal = PerAssetThresholdCalibrator(db=db)  # enabled env'den okur
        await cal.warm_cache()
        # Her tick:
        t = cal.get_threshold(instance_id, 'ae')
        is_anom = (
            engine.is_anomaly(features)  # default global
            if t is None
            else is_anomaly_at_threshold(score, 'ae', t)
        )

    Disabled iken (``enabled=False`` veya env 'off'): ``warm_cache`` ve
    ``calibrate`` no-op; ``get_threshold`` hep None. Caller fallback'e gecer.
    """

    def __init__(
        self,
        db: DatabaseInterface,
        *,
        enabled: bool | None = None,
    ) -> None:
        """Calibrator kurulumu.

        ``enabled`` None ise env (``CUSTOS_PER_ASSET_THRESHOLD``) okunur
        (default 'off'). Explicit deger (test/dev override) env'i yener.
        """
        self._db = db
        self._enabled = enabled if enabled is not None else resolve_enabled()
        # In-memory cache: (asset_id, engine_type) → AssetThreshold.
        # Senkron get_threshold icin warm_cache ile doldurulur.
        self._cache: dict[tuple[int, str], AssetThreshold] = {}

    @property
    def enabled(self) -> bool:
        """Master switch (env / explicit). Test + log icin read-only."""
        return self._enabled

    @property
    def cache_size(self) -> int:
        """Cache'deki kayit sayisi (debug + metric icin)."""
        return len(self._cache)

    async def warm_cache(self) -> int:
        """Tum threshold'lari DB'den yukler.

        Disabled iken no-op (sifir doner). Tablo yoksa exception caller'a
        yansir; bu sayede sessiz AVM uygulamasi yakalanir (early fail).
        Donus: yuklenen kayit sayisi.
        """
        if not self._enabled:
            return 0
        rows = await self._db.list_asset_thresholds()
        self._cache.clear()
        for row in rows:
            self._cache[(row.asset_instance_id, row.engine_type)] = row
        await logger.ainfo(
            "Per-asset threshold cache yuklendi",
            count=len(self._cache),
        )
        return len(self._cache)

    def get_threshold(
        self,
        asset_instance_id: int,
        engine_type: str,
    ) -> float | None:
        """Cache'den threshold doner (senkron — tight loop friendly).

        Cache miss → None (caller fallback'e gecer). Disabled iken hep None.
        ``warm_cache()`` cagrilmamissa cache bostur, hepsi None doner — bu
        davranis kasitli (init'te async cagri yapmamak icin).
        """
        if not self._enabled:
            return None
        entry = self._cache.get((asset_instance_id, engine_type))
        if entry is None:
            return None
        return entry.threshold

    def get_record(
        self,
        asset_instance_id: int,
        engine_type: str,
    ) -> AssetThreshold | None:
        """Cache'deki tum kaydi doner (debug + admin UI icin)."""
        if not self._enabled:
            return None
        return self._cache.get((asset_instance_id, engine_type))

    async def calibrate(
        self,
        asset_instance_id: int,
        engine_type: str,
        training_scores: np.ndarray,
        quantile: float | None = None,
    ) -> AssetThreshold:
        """Asset + engine icin threshold hesaplar ve DB'ye yazar.

        Algoritma::

            t = np.quantile(training_scores, quantile)

        Geriye yazilan kayit + cache guncellenir. Disabled iken
        ``RuntimeError`` firlatir — calibrator devre disi iken yazma
        cagrisi kullanici hatasi (sessizce yutmak hata gizler).

        ``quantile`` None ise ``default_quantile(engine_type)`` (AE=0.99,
        IF=0.01).
        """
        if not self._enabled:
            msg = (
                f"PerAssetThresholdCalibrator disabled "
                f"(env {_ENABLED_ENV}); calibrate cagrilamaz."
            )
            raise RuntimeError(msg)

        if engine_type not in ASSET_THRESHOLD_ENGINE_TYPE_VALUES:
            msg = (
                f"Bilinmeyen engine_type={engine_type!r}; "
                f"izinli: {sorted(ASSET_THRESHOLD_ENGINE_TYPE_VALUES)}"
            )
            raise ValueError(msg)

        scores = np.asarray(training_scores, dtype=np.float64)
        if scores.ndim != 1:
            msg = f"training_scores 1D olmali, geldi: shape={scores.shape}"
            raise ValueError(msg)
        if scores.size < MIN_TRAINING_SCORES:
            msg = (
                f"Yetersiz training_scores: {scores.size} satir "
                f"(min {MIN_TRAINING_SCORES})"
            )
            raise ValueError(msg)
        if not np.all(np.isfinite(scores)):
            # NaN/inf quantile'i bozar; caller temizlemeli.
            msg = "training_scores NaN/inf icermemeli (caller temizlemeli)"
            raise ValueError(msg)

        q = quantile if quantile is not None else default_quantile(engine_type)
        if not 0.0 < q < 1.0:
            msg = f"quantile (0, 1) araliginda olmali, geldi: {q}"
            raise ValueError(msg)

        threshold_value = float(np.quantile(scores, q))

        record = AssetThreshold(
            asset_instance_id=asset_instance_id,
            engine_type=engine_type,
            threshold=threshold_value,
            training_quantile=q,
            sample_count=int(scores.size),
            calibrated_at=datetime.now(UTC),
        )
        persisted = await self._db.upsert_asset_threshold(record)
        # Cache'i guncel sonuçla (DB'nin atadigi id + timestamp'ler ile) yenile.
        self._cache[(asset_instance_id, engine_type)] = persisted
        await logger.ainfo(
            "Per-asset threshold kalibre edildi",
            asset_instance_id=asset_instance_id,
            engine_type=engine_type,
            threshold=threshold_value,
            quantile=q,
            sample_count=int(scores.size),
        )
        return persisted

    def invalidate(self, asset_instance_id: int, engine_type: str) -> None:
        """Cache'den tek bir kaydi cikar (re-kalibrasyon arifesinde)."""
        self._cache.pop((asset_instance_id, engine_type), None)

    def invalidate_all(self) -> None:
        """Cache'i tamamen temizler (test + admin reset)."""
        self._cache.clear()


__all__ = [
    "DEFAULT_QUANTILE_AE",
    "DEFAULT_QUANTILE_IF",
    "MIN_TRAINING_SCORES",
    "PerAssetThresholdCalibrator",
    "default_quantile",
    "is_anomaly_at_threshold",
    "resolve_enabled",
]
