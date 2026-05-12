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
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from custos.analytics.care_scorer import (
    CAREResult,
    CAREScorer,
    Event,
)

if TYPE_CHECKING:
    from numpy.typing import NDArray

logger = logging.getLogger("validate_care")

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
    """Yuklenmis CSV + status + feature matrix."""

    info: DatasetInfo
    features: NDArray[np.float64]   # shape (n_rows, n_features)
    status: NDArray[np.int_]        # shape (n_rows,)


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

    return LoadedDataset(info=info, features=features, status=status)


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


def build_runs(
    datasets: list[LoadedDataset],
    models_dir: Path,
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

    # 3) IF ve AE — per-dataset model yuklemesi
    per_dataset_preds: dict[str, list[NDArray[np.int_]]] = {
        "isolation_forest": [],
        "autoencoder": [],
    }
    for engine_name, predict_fn in (
        ("isolation_forest", predictions_isolation_forest),
        ("autoencoder", predictions_autoencoder),
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

    # 4) Combined engine — IF OR AE (anomaly_detector engine_mode='both' davranisi).
    #    Her dataset icin her iki engine de varsa, bool OR; biri yoksa sadece
    #    digerinin tahmini gecerli olur.
    if_chunks = per_dataset_preds["isolation_forest"]
    ae_chunks = per_dataset_preds["autoencoder"]
    combined_chunks: list[NDArray[np.int_]] = []
    combined_skipped = 0
    for if_arr, ae_arr in zip(if_chunks, ae_chunks, strict=True):
        if if_arr.sum() == 0 and ae_arr.sum() == 0:
            # Ikisi de bos / model yok — skipped sayilir (tahmin yok)
            combined_skipped += 1
        # OR — herhangi biri 1 ise combined 1
        combined_chunks.append(((if_arr.astype(bool)) | (ae_arr.astype(bool))).astype(np.int_))
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

    paper_baseline = {
        "random": "Paper ≈ 0.50",
        "all_anomaly": "Paper = 0 (Acc<0.5 fallback)",
        "all_normal": "Paper = 0 (no positive)",
        "isolation_forest": "Paper ≈ 0.40-0.45",
        "autoencoder": "Paper ≈ 0.66",
        "combined": "Custos dual-engine (IF OR AE)",
    }

    order = [
        "random",
        "all_anomaly",
        "all_normal",
        "isolation_forest",
        "autoencoder",
        "combined",
    ]
    for engine_name in order:
        if engine_name not in runs:
            continue
        run = runs[engine_name]
        if run.n_skipped == run.n_datasets and engine_name in {
            "isolation_forest", "autoencoder", "combined",
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
    runs = build_runs(datasets, models_dir)
    report = render_markdown_report(runs, scorer, datasets)

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
