"""Wind Farm A dataset'leri uzerinde IF + AE modellerini CARE ile dogrular.

Wind pivot Faz 1.4 (2026-05-12). Mevcut Custos anomaly engine'leri
(Isolation Forest + sklearn Autoencoder) ile Fraunhofer paper'inin
3 baseline'ini (Random, All-Anomaly, All-Normal) ayni metrik sistemi
altinda karsilastirir; ciktiyi markdown tablosu olarak yazar.

Calistirma::

    set -a; source _personal/wind_pivot/.env.wind; set +a
    .venv/bin/python scripts/validate_models_on_care.py \\
        --datasets-dir _personal/wind_pivot/raw/CARE_To_Compare/Wind\\ Farm\\ A/datasets \\
        --event-info  _personal/wind_pivot/raw/CARE_To_Compare/Wind\\ Farm\\ A/event_info.csv \\
        --tag-map     _personal/wind_pivot/tag_map_farm_a.csv \\
        --models-dir  data/models \\
        --report-path _personal/wind_pivot/reports/03_care_results.md \\
        --limit 22

Cikti dosyasi varsa uzerine yazilir. Eksik model dosyasi varsa o engine
tablosu 'N/A' isaretlenir; baseline'lar her zaman uretilebilir.

Veri yapisi varsayimi (Fraunhofer CARE Wind Farm A):
* ``datasets/<event_id>.csv`` — semicolon, 86 sensor + status_type_id +
  zaman damgasi. Her dosya bir event/dataset'i temsil eder.
* ``event_info.csv`` — semicolon, ``event_id;event_label;event_start_id;
  event_end_id;asset;event_description`` (ek alanlar tolere edilir).
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from custos.analytics.care_scorer import (
    CAREResult,
    CAREScorer,
    Event,
)
from custos.analytics.cross_sensor_engine import (
    CrossSensorEngine,
)
from custos.analytics.cross_sensor_engine import (
    resolve_enabled as resolve_cross_sensor_enabled,
)
from custos.analytics.trend_monitor import (
    DEFAULT_EWMA_ALPHA,
    DEFAULT_MIN_OBSERVATIONS,
    DEFAULT_SLOPE_THRESHOLD,
    DEFAULT_WINDOW_SIZE,
    TrendMonitor,
)

if TYPE_CHECKING:
    from numpy.typing import NDArray

logger = logging.getLogger("validate_care")

# Faz 2 P0 — Trend monitor "tick" cikti zamanlama esasi (sentetik base ts;
# CARE dataset'lerinde wall-clock yerine pozisyon-tabanli analiz yapildigi
# icin gercek tarih anlami yok, sadece monotonik tick gerekiyor).
_TREND_BASE_TS = datetime(2026, 1, 1, tzinfo=UTC)
_TREND_TICK_MINUTES = 10

# Default limit — paper Wind Farm A reduced subset (22 dataset secimi
# proje-spesifik; tum 89 dataset icin --limit 0).
DEFAULT_LIMIT = 22

# Status_type_id kolonu ismi (Fraunhofer CSV semantigi).
STATUS_COLUMN = "status_type_id"

# Feature olarak SAYILMAYAN metadata kolonlari. ``id`` ve ``train_test``
# Faz 1.5 kapanis bug-fix'i ile dahil edildi (training script ile uyumlu olmali —
# yoksa feature sayisi 81 vs 83 uyumsuzlugu modelleri sessiz devre disi birakiyor).
META_COLUMNS: frozenset[str] = frozenset({
    STATUS_COLUMN,
    "time_stamp",
    "timestamp",
    "asset_id",
    "id",
    "train_test",
})

# Random baseline icin tohum — reproducability icin.
RANDOM_SEED = 1729

# Default rastgele alarm orani (Bernoulli p). Paper 50/50 baseline.
RANDOM_PROB = 0.5


@dataclass(frozen=True)
class DatasetInfo:
    """Bir CARE dataset'inin runtime ozeti."""

    event_id: int
    csv_path: Path
    label: str
    event_start_id: int  # CSV satir indeksi (dataset icindeki)
    event_end_id: int    # CSV satir indeksi (dataset icindeki)
    asset: str


@dataclass(frozen=True)
class LoadedDataset:
    """Yuklenmis CSV + status + feature matrix.

    ``sensor_columns`` features matrix'inin kolon adlari (Fraunhofer
    ``sensor_X_avg`` konvansiyonu). Wind pivot Faz 2 Prompt 2 ile eklendi —
    cross_sensor engine bu adlari tag_map ile esleyip kural-tag → kolon
    indeksi map'i kuruyor.
    """

    info: DatasetInfo
    features: NDArray[np.float64]   # shape (n_rows, n_features)
    status: NDArray[np.int_]        # shape (n_rows,)
    sensor_columns: tuple[str, ...] = ()


# --- event_info + dataset yukleyiciler ---


def load_event_info(path: Path) -> list[DatasetInfo]:
    """event_info.csv → DatasetInfo listesi (csv_path daha sonra atanir).

    Sira event_id'ye gore degil dosya sirasina gore korunur; caller
    isteyene gore filtreler/siralanir.
    """
    base = path.parent
    csv_dir = base / "datasets"
    infos: list[DatasetInfo] = []
    with path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            ev_id = int(row["event_id"])
            csv_path = csv_dir / f"{ev_id}.csv"
            infos.append(
                DatasetInfo(
                    event_id=ev_id,
                    csv_path=csv_path,
                    label=row["event_label"].strip(),
                    event_start_id=int(row["event_start_id"]),
                    event_end_id=int(row["event_end_id"]),
                    asset=row.get("asset", "").strip(),
                ),
            )
    return infos


def load_dataset(info: DatasetInfo) -> LoadedDataset | None:
    """Bir dataset CSV'sini yukler; eksik dosya → None (skip).

    Tum sensor kolonlari ``status_type_id`` haric float'a cevrilir;
    NaN olanlar 0.0 ile doldurulur (AE NaN kabul etmez). Status kolonu
    bulunamazsa 0 (Normal) varsayilir.
    """
    if not info.csv_path.exists():
        logger.warning(
            "Dataset CSV bulunamadi (skip): %s", info.csv_path,
        )
        return None

    sensor_cols: list[str] = []
    rows: list[dict[str, str]] = []
    with info.csv_path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter=";")
        sensor_cols = [
            c for c in (reader.fieldnames or [])
            if c not in META_COLUMNS
        ]
        rows = list(reader)
    if not rows:
        logger.warning("Bos dataset (skip): %s", info.csv_path)
        return None

    n = len(rows)
    features = np.zeros((n, len(sensor_cols)), dtype=np.float64)
    status = np.zeros(n, dtype=np.int_)
    for i, row in enumerate(rows):
        for j, col in enumerate(sensor_cols):
            features[i, j] = _safe_float(row.get(col, ""))
        status[i] = _safe_int(row.get(STATUS_COLUMN, "0"))

    return LoadedDataset(
        info=info,
        features=features,
        status=status,
        sensor_columns=tuple(sensor_cols),
    )


def _safe_float(value: str | None) -> float:
    """CSV float parse — bos / 'nan' → 0.0 (AE friendly)."""
    if value is None:
        return 0.0
    s = value.strip()
    if not s or s.lower() in {"nan", "null", "none"}:
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def _safe_int(value: str | None) -> int:
    """CSV int parse — bos / hata → 0 (Normal status)."""
    if value is None:
        return 0
    s = value.strip()
    if not s:
        return 0
    try:
        return int(s)
    except ValueError:
        return 0


# --- Tahmin uretici ---


def predictions_random(
    n: int,
    seed: int = RANDOM_SEED,
    prob: float = RANDOM_PROB,
) -> NDArray[np.int_]:
    """Bernoulli(prob) rastgele 0/1 — paper Random baseline."""
    rng = np.random.default_rng(seed)
    return (rng.random(n) < prob).astype(np.int_)


def predictions_all_anomaly(n: int) -> NDArray[np.int_]:
    """Surekli 1 — paper All-Anomaly baseline."""
    return np.ones(n, dtype=np.int_)


def predictions_all_normal(n: int) -> NDArray[np.int_]:
    """Surekli 0 — paper All-Normal baseline."""
    return np.zeros(n, dtype=np.int_)


def predictions_isolation_forest(
    features: NDArray[np.float64],
    models_dir: Path,
    asset: str,
) -> NDArray[np.int_] | None:
    """Asset'in IF modelini yukle ve tahmin uret.

    Model yolu: ``data/models/anomaly_<asset>.joblib`` (asset adi int olarak
    yorumlanabilirse) veya isim olarak. Model yoksa None.
    """
    candidates: list[Path] = []
    asset_int = _try_int(asset)
    if asset_int is not None:
        candidates.append(models_dir / f"anomaly_{asset_int}.joblib")
    candidates.append(models_dir / f"anomaly_{asset}.joblib")
    model_path = next((p for p in candidates if p.exists()), None)
    if model_path is None:
        logger.warning("IF modeli bulunamadi (asset=%s): %s", asset, candidates)
        return None
    import joblib  # noqa: PLC0415

    model = joblib.load(model_path)
    n_features_expected = getattr(model, "n_features_in_", features.shape[1])
    if features.shape[1] != n_features_expected:
        logger.warning(
            "IF feature sayisi uyumsuz asset=%s: data=%d, model=%d",
            asset, features.shape[1], n_features_expected,
        )
        return None
    # IsolationForest.predict → -1 (anomaly) | +1 (normal); biz 0/1'e cevirelim.
    raw = model.predict(features)
    result: NDArray[np.int_] = (raw == -1).astype(np.int_)
    return result


def predictions_autoencoder(
    features: NDArray[np.float64],
    models_dir: Path,
    asset: str,
) -> NDArray[np.int_] | None:
    """Asset'in AE modelini yukle ve tahmin uret (RMSE > threshold)."""
    candidates: list[Path] = []
    asset_int = _try_int(asset)
    if asset_int is not None:
        candidates.append(models_dir / f"autoencoder_{asset_int}_wind.joblib")
    candidates.append(models_dir / f"autoencoder_{asset}_wind.joblib")
    model_path = next((p for p in candidates if p.exists()), None)
    if model_path is None:
        logger.warning("AE modeli bulunamadi (asset=%s): %s", asset, candidates)
        return None

    from custos.analytics.autoencoder_engine import AutoencoderAnomalyEngine  # noqa: PLC0415

    engine = AutoencoderAnomalyEngine.load(model_path)
    if features.shape[1] != engine.n_features:
        logger.warning(
            "AE feature sayisi uyumsuz asset=%s: data=%d, model=%d",
            asset, features.shape[1], engine.n_features,
        )
        return None
    return engine.is_anomaly(features).astype(np.int_)


def _try_int(value: str) -> int | None:
    """'1' → 1, 'WT1' → None. Joblib dosya adlandirma icin esnek lookup."""
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def load_tag_map(path: Path) -> dict[str, str]:
    """tag_map_farm_a.csv'yi yukler — custos_tag_name → sensor_name (Fraunhofer).

    YAML cross_sensor_rules ``wind_t_*`` custos isimleri kullanir; CARE
    dataset kolonlari ``sensor_X_avg`` Fraunhofer isimleridir. Bu reverse
    map cross_sensor evaluation icin koprudur.

    Eksik dosya → ``FileNotFoundError``. CSV bos veya gerekli kolon yok
    → ``ValueError``.
    """
    if not path.is_file():
        msg = f"tag_map dosyasi bulunamadi: {path}"
        raise FileNotFoundError(msg)
    reverse: dict[str, str] = {}
    with path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None or (
            "custos_tag_name" not in reader.fieldnames
            or "sensor_name" not in reader.fieldnames
        ):
            msg = (
                f"tag_map gerekli kolonlari icermiyor "
                f"('custos_tag_name' + 'sensor_name'): {reader.fieldnames}"
            )
            raise ValueError(msg)
        for row in reader:
            custos = (row.get("custos_tag_name") or "").strip()
            sensor = (row.get("sensor_name") or "").strip()
            if custos and sensor:
                reverse[custos] = sensor
    if not reverse:
        msg = f"tag_map bos: {path}"
        raise ValueError(msg)
    return reverse


def load_cross_sensor_engine(
    asset_template_path: Path | None,
) -> CrossSensorEngine | None:
    """Asset template'inden ``CrossSensorEngine`` olusturur (env-gated).

    Wind pivot Faz 2 Prompt 2 mini-edit (2026-05-12): ``CUSTOS_CROSS_SENSOR``
    env var 'off' (default) ise None doner — engine yuklenmez, Combined
    sonuca dahil edilmez. CARE benchmark'ında Wind Farm A Portekiz iklim
    baseline'inin Custos kural esikleriyle uyumsuz oldugu (Combined CARE
    0.530 → 0.500 regresyon) gosterildi; default OFF saha kalibrasyonu
    olmadan engine'in regresyon uretmesini engeller.

    Yol None veya dosya yoksa None doner (caller cross_sensor engine'i
    devre disi birakir, mevcut combined IF/AE/Trend bozulmaz). YAML
    parsing hatasi caller'a yansir (config hatasi sessiz gecmesin).
    """
    if not resolve_cross_sensor_enabled():
        logger.info(
            "Cross-sensor engine disabled by env CUSTOS_CROSS_SENSOR=off "
            "(default — saha kalibrasyonu sonrasi pilotta 'on' yapilir)",
        )
        return None
    if asset_template_path is None:
        return None
    if not asset_template_path.is_file():
        logger.warning(
            "Asset template bulunamadi (cross_sensor skipped): %s",
            asset_template_path,
        )
        return None
    return CrossSensorEngine.from_yaml_file(asset_template_path)


def build_cross_sensor_columns_map(
    sensor_columns: list[str],
    tag_map: dict[str, str],
    engine: CrossSensorEngine,
) -> dict[str, int]:
    """Engine'in baktigi her ``wind_t_*`` tag'i CARE feature kolonuna esleyen
    map'i hazirlar.

    sensor_columns: dataset feature matrix'inin kolon adlari sirasi
    (Fraunhofer isimleriyle). tag_map: custos_tag_name → sensor_name.
    Eksik tag (CARE dataset'inde kolon yoksa veya tag_map'de yer almiyorsa)
    sessizce atlanir — kural ANDed ise eksik tag = False, evaluation
    erken-cikar.
    """
    sensor_index = {name: i for i, name in enumerate(sensor_columns)}
    out: dict[str, int] = {}
    for custos_name in engine.required_tags:
        sensor_name = tag_map.get(custos_name)
        if sensor_name is None:
            logger.debug(
                "Cross-sensor tag tag_map'de yok (atlandi): %s",
                custos_name,
            )
            continue
        idx = sensor_index.get(sensor_name)
        if idx is None:
            logger.debug(
                "Cross-sensor sensor_name dataset kolonunda yok (atlandi): "
                "%s → %s", custos_name, sensor_name,
            )
            continue
        out[custos_name] = idx
    return out


def predictions_cross_sensor(
    features: NDArray[np.float64],
    sensor_columns: list[str],
    engine: CrossSensorEngine | None,
    tag_map: dict[str, str],
) -> NDArray[np.int_] | None:
    """Bir dataset features matrix'i icin cross_sensor 0/1 predictions uretir.

    Engine yoksa veya hicbir kuralin tag'i CARE dataset'inde bulunamiyorsa
    None doner (caller skip eder, runs[cross_sensor].n_skipped++).
    Aksi halde her satir icin herhangi bir kural tetiklenirse 1, yoksa 0.
    """
    if engine is None or engine.n_rules == 0:
        return None
    tag_columns_map = build_cross_sensor_columns_map(
        sensor_columns, tag_map, engine,
    )
    if not tag_columns_map:
        logger.warning(
            "Cross-sensor engine kurallarinin hicbir tag'i CARE "
            "dataset'inde bulunamadi — engine skipped",
        )
        return None
    preds_list = engine.evaluate_history(features, tag_columns_map)
    return np.asarray(preds_list, dtype=np.int_)


def predictions_trend_monitor(
    features: NDArray[np.float64],
    models_dir: Path,
    asset: str,
    *,
    window_size: int = DEFAULT_WINDOW_SIZE,
    ewma_alpha: float = DEFAULT_EWMA_ALPHA,
    slope_threshold: float = DEFAULT_SLOPE_THRESHOLD,
    min_observations: int = DEFAULT_MIN_OBSERVATIONS,
) -> NDArray[np.int_] | None:
    """AE raw skoru uzerinden TrendMonitor predictions uretir.

    Faz 2 P0: Yavas yukari trend (mekanik bearing arizasi) yakalamak icin
    AE reconstruction error (RMSE) EWMA'lanir, slope esigi asilirsa
    prediction=1. AE modeli yoksa veya feature uyumsuzsa None doner.

    Caller (build_runs) bunu IF + AE ile birlestirir; Combined engine
    artik **3 sinyalin OR'u** olarak hesaplanir.
    """
    candidates: list[Path] = []
    asset_int = _try_int(asset)
    if asset_int is not None:
        candidates.append(models_dir / f"autoencoder_{asset_int}_wind.joblib")
    candidates.append(models_dir / f"autoencoder_{asset}_wind.joblib")
    model_path = next((p for p in candidates if p.exists()), None)
    if model_path is None:
        logger.warning(
            "Trend monitor: AE modeli bulunamadi (asset=%s): %s",
            asset, candidates,
        )
        return None

    from custos.analytics.autoencoder_engine import (  # noqa: PLC0415
        AutoencoderAnomalyEngine,
    )

    engine = AutoencoderAnomalyEngine.load(model_path)
    if features.shape[1] != engine.n_features:
        logger.warning(
            "Trend monitor AE feature sayisi uyumsuz asset=%s: data=%d, model=%d",
            asset, features.shape[1], engine.n_features,
        )
        return None

    # AE raw RMSE — sigmoid yok, direct error sinyali (yukari = saglik bozulmasi).
    raw_scores = engine.score(features)

    mon = TrendMonitor(
        window_size=window_size,
        ewma_alpha=ewma_alpha,
        slope_threshold=slope_threshold,
        min_observations=min_observations,
    )
    # Per-dataset reset varsayilan — her dataset'i bagimsiz isle. Sentetik
    # asset_id 1 (tek dataset icin yeterli, multi-asset state collision yok).
    asset_id_pseudo = 1
    preds = np.zeros(features.shape[0], dtype=np.int_)
    for i, score in enumerate(raw_scores):
        ts = _TREND_BASE_TS + timedelta(minutes=_TREND_TICK_MINUTES * i)
        alert = mon.update(asset_id_pseudo, ts, float(score))
        if alert is not None:
            preds[i] = 1
    return preds


# --- Orkestrator ---


@dataclass
class EngineRun:
    """Bir engine'in tum dataset'lerdeki birlestirilmis ciktisini tasir."""

    name: str
    predictions: NDArray[np.int_]
    status: NDArray[np.int_]
    events: list[Event]
    n_datasets: int
    n_skipped: int

    def to_care(self, scorer: CAREScorer) -> CAREResult:
        """Birlestirilmis preds + events ile CARE skoru uretir."""
        return scorer.score(
            self.predictions,
            self.events,
            status_types=self.status,
        )


@dataclass(frozen=True)
class TrendConfig:
    """TrendMonitor hyperparametre demeti — CLI override icin (Faz 2 P0).

    Default'lar trend_monitor modul sabitleriyle ayni; saha kalibrasyonu
    icin CLI --trend-* argumanlariyla override edilir.
    """

    window_size: int = DEFAULT_WINDOW_SIZE
    ewma_alpha: float = DEFAULT_EWMA_ALPHA
    slope_threshold: float = DEFAULT_SLOPE_THRESHOLD
    min_observations: int = DEFAULT_MIN_OBSERVATIONS


def build_runs(
    datasets: list[LoadedDataset],
    models_dir: Path,
    trend_config: TrendConfig | None = None,
    *,
    cross_sensor_engine: CrossSensorEngine | None = None,
    tag_map: dict[str, str] | None = None,
) -> dict[str, EngineRun]:
    """Tum engine'ler icin birlesik tahmin akisini hazirlar.

    Birlestirme: dataset'ler sirayla concat edilir; her dataset bir Event
    objesi olarak indekslenir. Anomaly event'lerinde fault window dataset
    CSV'sindeki event_start_id/end_id'ye karsilik gelen offset'lere kayar.
    """
    if not datasets:
        return {}

    # 1) Status + concatenated indeksleme
    all_status_chunks: list[NDArray[np.int_]] = []
    all_events: list[Event] = []
    n_features = datasets[0].features.shape[1]
    cursor = 0
    for ds in datasets:
        n_rows = ds.features.shape[0]
        all_status_chunks.append(ds.status)
        ds_start = cursor
        ds_end = cursor + n_rows - 1
        if ds.info.label == "anomaly":
            event_start = ds_start + ds.info.event_start_id
            event_end = ds_start + ds.info.event_end_id
            # Bazi CSV'lerde event_end > n_rows-1 olabilir; sinira clip
            event_end = min(event_end, ds_end)
            event_start = max(event_start, ds_start)
            all_events.append(
                Event(
                    event_id=ds.info.event_id,
                    label="anomaly",
                    event_start_id=event_start,
                    event_end_id=event_end,
                    dataset_start_id=ds_start,
                    dataset_end_id=ds_end,
                ),
            )
        else:
            all_events.append(
                Event(
                    event_id=ds.info.event_id,
                    label="normal",
                    event_start_id=ds_start,
                    event_end_id=ds_end,
                ),
            )
        cursor += n_rows

    status_all: NDArray[np.int_] = np.concatenate(all_status_chunks)
    n_total = int(status_all.shape[0])

    runs: dict[str, EngineRun] = {}

    # 2) Sentetik baseline'lar (her zaman uretilebilir)
    baseline_preds: dict[str, NDArray[np.int_]] = {
        "random": predictions_random(n_total),
        "all_anomaly": predictions_all_anomaly(n_total),
        "all_normal": predictions_all_normal(n_total),
    }
    for name, preds in baseline_preds.items():
        runs[name] = EngineRun(
            name=name,
            predictions=preds,
            status=status_all,
            events=all_events,
            n_datasets=len(datasets),
            n_skipped=0,
        )

    # 3) IF, AE, Trend Monitor — per-dataset model yuklemesi.
    # Faz 2 P0: trend_monitor AE skoru uzerinden EWMA-slope analizi yapar.
    cfg = trend_config if trend_config is not None else TrendConfig()

    def _predict_trend(
        features: NDArray[np.float64],
        models_dir: Path,
        asset: str,
    ) -> NDArray[np.int_] | None:
        return predictions_trend_monitor(
            features,
            models_dir,
            asset,
            window_size=cfg.window_size,
            ewma_alpha=cfg.ewma_alpha,
            slope_threshold=cfg.slope_threshold,
            min_observations=cfg.min_observations,
        )

    per_dataset_preds: dict[str, list[NDArray[np.int_]]] = {
        "isolation_forest": [],
        "autoencoder": [],
        "trend_monitor": [],
    }
    for engine_name, predict_fn in (
        ("isolation_forest", predictions_isolation_forest),
        ("autoencoder", predictions_autoencoder),
        ("trend_monitor", _predict_trend),
    ):
        skipped = 0
        for ds in datasets:
            preds_opt = predict_fn(ds.features, models_dir, ds.info.asset)
            if preds_opt is None:
                # Model yok ya da uyumsuz — bu dataset tahminleri 0
                preds_arr: NDArray[np.int_] = np.zeros(
                    ds.features.shape[0], dtype=np.int_,
                )
                skipped += 1
            else:
                preds_arr = preds_opt
            per_dataset_preds[engine_name].append(preds_arr)
        runs[engine_name] = EngineRun(
            name=engine_name,
            predictions=np.concatenate(per_dataset_preds[engine_name]),
            status=status_all,
            events=all_events,
            n_datasets=len(datasets),
            n_skipped=skipped,
        )

    # 4) Cross-sensor engine (Wind pivot Faz 2 Prompt 2). YAML kurallarini
    # her dataset features matrix'inde tek-tick row-bazinda degerlendirir.
    cs_chunks: list[NDArray[np.int_]] = []
    cs_skipped = 0
    if cross_sensor_engine is not None and tag_map is not None:
        for ds in datasets:
            preds_cs = predictions_cross_sensor(
                ds.features,
                list(ds.sensor_columns),
                cross_sensor_engine,
                tag_map,
            )
            if preds_cs is None:
                preds_cs = np.zeros(ds.features.shape[0], dtype=np.int_)
                cs_skipped += 1
            cs_chunks.append(preds_cs)
    else:
        # Engine veya tag_map yok — tum dataset'leri 0 ile doldur ve skip
        # say. runs["cross_sensor"] yine olusur (raporda 'N/A' satiri).
        for ds in datasets:
            cs_chunks.append(np.zeros(ds.features.shape[0], dtype=np.int_))
        cs_skipped = len(datasets)

    runs["cross_sensor"] = EngineRun(
        name="cross_sensor",
        predictions=np.concatenate(cs_chunks),
        status=status_all,
        events=all_events,
        n_datasets=len(datasets),
        n_skipped=cs_skipped,
    )
    per_dataset_preds["cross_sensor"] = cs_chunks

    # 5) Combined engine — IF OR AE OR Trend OR CrossSensor (Faz 2 Prompt 2:
    # 4 sinyal birlesimi). Anomaly detector engine_mode='both' +
    # CUSTOS_TREND_MONITOR=on + cross_sensor engine yuklu davranisi.
    if_chunks = per_dataset_preds["isolation_forest"]
    ae_chunks = per_dataset_preds["autoencoder"]
    trend_chunks = per_dataset_preds["trend_monitor"]
    combined_chunks: list[NDArray[np.int_]] = []
    combined_skipped = 0
    for if_arr, ae_arr, trend_arr, cs_arr in zip(
        if_chunks, ae_chunks, trend_chunks, cs_chunks, strict=True,
    ):
        if (
            if_arr.sum() == 0
            and ae_arr.sum() == 0
            and trend_arr.sum() == 0
            and cs_arr.sum() == 0
        ):
            # Hicbiri pozitif uretmedi — skipped sayilir (model yok varsayimi)
            combined_skipped += 1
        # OR — herhangi biri 1 ise combined 1
        combined_chunks.append(
            (
                if_arr.astype(bool)
                | ae_arr.astype(bool)
                | trend_arr.astype(bool)
                | cs_arr.astype(bool)
            ).astype(np.int_),
        )
    runs["combined"] = EngineRun(
        name="combined",
        predictions=np.concatenate(combined_chunks),
        status=status_all,
        events=all_events,
        n_datasets=len(datasets),
        n_skipped=combined_skipped,
    )

    _ = n_features  # placeholder — ileride sanity check icin
    return runs


# --- Rapor uretici ---


def render_markdown_report(
    runs: dict[str, EngineRun],
    scorer: CAREScorer,
    datasets: list[LoadedDataset],
    trend_config: TrendConfig | None = None,
) -> str:
    """Markdown tablosu olarak rapor uretir.

    Tablo: Engine | Coverage | Accuracy | Reliability | Earliness | CARE | Not
    """
    lines: list[str] = []
    lines.append("# CARE Benchmark Sonuclari — Wind Farm A")
    lines.append("")
    lines.append(
        "Custos anomaly detection performansinin Fraunhofer CARE paper "
        "(arXiv:2404.10320v2) baseline'lariyla karsilastirmasi.",
    )
    lines.append("")
    lines.append("## Setup")
    lines.append("")
    lines.append(f"- Dataset sayisi: **{len(datasets)}**")
    n_anomaly = sum(1 for d in datasets if d.info.label == "anomaly")
    n_normal = sum(1 for d in datasets if d.info.label == "normal")
    lines.append(
        f"- Anomaly event: **{n_anomaly}**, Normal dataset: **{n_normal}**",
    )
    lines.append(
        f"- CARE parametreleri: tc={scorer.tc}, beta={scorer.beta}, "
        f"weights=(cov={scorer.weights[0]}, earl={scorer.weights[1]}, "
        f"rel={scorer.weights[2]}, acc={scorer.weights[3]})",
    )
    if trend_config is not None:
        lines.append(
            f"- Trend monitor: window={trend_config.window_size}, "
            f"alpha={trend_config.ewma_alpha}, "
            f"slope_thr={trend_config.slope_threshold}, "
            f"min_obs={trend_config.min_observations}",
        )
    lines.append("")
    lines.append("## Sonuc Tablosu")
    lines.append("")
    lines.append(
        "| Engine | Coverage | Accuracy | Reliability | Earliness | "
        "**CARE** | Not |",
    )
    lines.append(
        "|--------|---------:|---------:|------------:|----------:|"
        "---------:|-----|",
    )

    cross_sensor_off = not resolve_cross_sensor_enabled()
    combined_note = (
        "Custos tri-engine (IF OR AE OR Trend) — cross_sensor disabled by env"
        if cross_sensor_off
        else "Custos quad-engine (IF OR AE OR Trend OR CrossSensor)"
    )
    paper_baseline = {
        "random": "Paper ≈ 0.50",
        "all_anomaly": "Paper = 0 (Acc<0.5 fallback)",
        "all_normal": "Paper = 0 (no positive)",
        "isolation_forest": "Paper ≈ 0.40-0.45",
        "autoencoder": "Paper ≈ 0.66",
        "trend_monitor": "Faz 2 P0 — EWMA slope (yavas trend)",
        "cross_sensor": "Faz 2 Prompt 2 — multi-tag AND (YAML kurallari)",
        "combined": combined_note,
    }

    order = [
        "random",
        "all_anomaly",
        "all_normal",
        "isolation_forest",
        "autoencoder",
        "trend_monitor",
        "cross_sensor",
        "combined",
    ]
    for engine_name in order:
        if engine_name not in runs:
            continue
        run = runs[engine_name]
        # Wind pivot Faz 2 Prompt 2 mini-edit — env-off durumunda
        # cross_sensor satirinin "disabled" gosterimi (Model bulunamadi
        # mesajindan ayrik).
        if engine_name == "cross_sensor" and cross_sensor_off:
            lines.append(
                f"| `{engine_name}` | N/A | N/A | N/A | N/A | **N/A** | "
                "disabled (env CUSTOS_CROSS_SENSOR=off) |",
            )
            continue
        if run.n_skipped == run.n_datasets and engine_name in {
            "isolation_forest",
            "autoencoder",
            "trend_monitor",
            "cross_sensor",
            "combined",
        }:
            lines.append(
                f"| `{engine_name}` | N/A | N/A | N/A | N/A | **N/A** | "
                f"Model bulunamadi (skipped {run.n_skipped}/{run.n_datasets}) |",
            )
            continue
        try:
            result = run.to_care(scorer)
        except ValueError as exc:
            lines.append(
                f"| `{engine_name}` | err | err | err | err | **err** | "
                f"{exc} |",
            )
            continue
        note = paper_baseline.get(engine_name, "")
        if run.n_skipped:
            note += f" — skipped: {run.n_skipped}"
        lines.append(
            f"| `{engine_name}` | {result.coverage:.3f} | "
            f"{result.accuracy:.3f} | {result.reliability:.3f} | "
            f"{result.earliness:.3f} | **{result.final:.3f}** | {note} |",
        )

    lines.append("")
    lines.append("## Implementasyon Notlari")
    lines.append("")
    lines.append(
        "* Paper status_type filtresi uygulanmistir (status ∈ {0, 2}).",
    )
    lines.append(
        "* Coverage / Earliness / Reliability tanimlari paper Section 3 + "
        "Eq. 1-5'ten dogrudan implement edildi.",
    )
    lines.append(
        "* Kritiklik sayaci: pred=1 → +1, pred=0 → max(c-1, 0). max(c) >= tc "
        "olunca event detected. Status filter ardisik sayim icinde "
        "uygulanmadi (paper Algorithm 1'in kesin versiyonu paper'da PDF "
        "olmadan dogrulanmasi guc; bizim implementasyon literal Turkce "
        "tarifle tutarli: ardisik 1'lere kucuk kesinti tolerans).",
    )
    lines.append(
        "* Belirsizlik: paper'in event_info.csv kolon adlari + dataset "
        "structure kanonik degil. Wind Farm A'da bizim okuma seman: "
        "`event_id;event_label;event_start_id;event_end_id` + datasets/<id>.csv.",
    )
    lines.append("")

    return "\n".join(lines) + "\n"


# --- CLI ---


def run(args: argparse.Namespace) -> int:
    """Orchestrator: event_info yukle → dataset'leri yukle → engine'leri "
    "kostur → rapor uret."""
    event_info_path = Path(args.event_info)
    if not event_info_path.exists():
        logger.error("event_info.csv bulunamadi: %s", event_info_path)
        return 2
    datasets_dir = (
        Path(args.datasets_dir)
        if args.datasets_dir
        else event_info_path.parent / "datasets"
    )
    models_dir = Path(args.models_dir)
    report_path = Path(args.report_path)

    infos = load_event_info(event_info_path)
    if args.limit > 0:
        infos = infos[: args.limit]
    if not infos:
        logger.error("Event listesi bos")
        return 1

    datasets: list[LoadedDataset] = []
    for info in infos:
        # csv_path event_info'da datasets/<id>.csv'ye isaret eder; override
        if args.datasets_dir:
            info = DatasetInfo(  # noqa: PLW2901
                event_id=info.event_id,
                csv_path=datasets_dir / f"{info.event_id}.csv",
                label=info.label,
                event_start_id=info.event_start_id,
                event_end_id=info.event_end_id,
                asset=info.asset,
            )
        ds = load_dataset(info)
        if ds is not None:
            datasets.append(ds)

    if not datasets:
        logger.error("Hicbir dataset yuklenemedi — script abort")
        return 1

    scorer = CAREScorer()
    trend_cfg = TrendConfig(
        window_size=args.trend_window,
        ewma_alpha=args.trend_alpha,
        slope_threshold=args.trend_threshold,
        min_observations=args.trend_min_obs,
    )
    # Wind pivot Faz 2 Prompt 2 — cross_sensor engine + tag_map yuklemesi.
    # Eksik dosya/arg → engine None, runs["cross_sensor"] N/A satiri yazar.
    asset_tmpl_path = (
        Path(args.asset_template) if args.asset_template else None
    )
    cross_sensor_engine = load_cross_sensor_engine(asset_tmpl_path)
    tag_map: dict[str, str] | None = None
    if args.tag_map:
        tag_map_path = Path(args.tag_map)
        try:
            tag_map = load_tag_map(tag_map_path)
        except (FileNotFoundError, ValueError) as exc:
            logger.warning(
                "tag_map yuklenemedi (cross_sensor skipped): %s", exc,
            )
            tag_map = None
    if cross_sensor_engine is not None:
        logger.info(
            "Cross-sensor engine yuklendi: %d kural, %d tag",
            cross_sensor_engine.n_rules,
            len(cross_sensor_engine.required_tags),
        )
    runs = build_runs(
        datasets,
        models_dir,
        trend_cfg,
        cross_sensor_engine=cross_sensor_engine,
        tag_map=tag_map,
    )
    report = render_markdown_report(runs, scorer, datasets, trend_cfg)

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    logger.info("Rapor yazildi: %s (%d dataset)", report_path, len(datasets))
    return 0


def build_argparser() -> argparse.ArgumentParser:
    """CLI argumanlari."""
    parser = argparse.ArgumentParser(
        description=(
            "Wind Farm A dataset'lerinde IF + AE + baseline'lari CARE ile "
            "dogrular ve markdown rapor uretir."
        ),
    )
    parser.add_argument(
        "--event-info",
        required=True,
        help="event_info.csv yolu (Fraunhofer Wind Farm A formatinda)",
    )
    parser.add_argument(
        "--datasets-dir",
        default=None,
        help=(
            "Dataset CSV klasoru. Verilmezse event_info.csv'nin yaninda "
            "'datasets/' bekler."
        ),
    )
    parser.add_argument(
        "--models-dir",
        default="data/models",
        help="Egitilmis IF/AE joblib dosyalarinin klasoru (default data/models)",
    )
    parser.add_argument(
        "--report-path",
        default="_personal/wind_pivot/reports/03_care_results.md",
        help="Cikti markdown raporu (default _personal/.../03_care_results.md)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=(
            f"En fazla bu kadar event yukle (default {DEFAULT_LIMIT}). "
            "0 → tum event'ler."
        ),
    )
    # Faz 2 P0 — Trend Monitor hyperparametre CLI override'lari.
    # AE RMSE olcek farkliligi nedeniyle default'lar saha verisinde
    # kalibre edilmelidir; CARE benchmark icin sweep yapilabilir.
    parser.add_argument(
        "--trend-window",
        type=int,
        default=DEFAULT_WINDOW_SIZE,
        help=f"Trend monitor slope lag window (tick). Default {DEFAULT_WINDOW_SIZE}.",
    )
    parser.add_argument(
        "--trend-alpha",
        type=float,
        default=DEFAULT_EWMA_ALPHA,
        help=f"Trend monitor EWMA alpha (0,1]. Default {DEFAULT_EWMA_ALPHA}.",
    )
    parser.add_argument(
        "--trend-threshold",
        type=float,
        default=DEFAULT_SLOPE_THRESHOLD,
        help=(
            f"Trend monitor slope esigi (birim/tick). "
            f"Default {DEFAULT_SLOPE_THRESHOLD}."
        ),
    )
    parser.add_argument(
        "--trend-min-obs",
        type=int,
        default=DEFAULT_MIN_OBSERVATIONS,
        help=(
            f"Trend monitor warmup (tick). Default {DEFAULT_MIN_OBSERVATIONS}."
        ),
    )
    # Wind pivot Faz 2 Prompt 2 — cross_sensor engine args.
    parser.add_argument(
        "--asset-template",
        default="data/asset_templates/wind_turbine_v1.yaml",
        help=(
            "Cross-sensor kurallarinin okunacagi asset template YAML "
            "(default wind_turbine_v1). Bos veya bulunamayan dosya → "
            "cross_sensor engine devre disi."
        ),
    )
    parser.add_argument(
        "--tag-map",
        default="_personal/wind_pivot/tag_map_farm_a.csv",
        help=(
            "Custos tag adi ↔ CARE sensor kolon esleme CSV'si "
            "(default tag_map_farm_a.csv). Eksikse cross_sensor skipped."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry-point."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = build_argparser()
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
