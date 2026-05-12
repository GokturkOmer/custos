"""AE hyperparameter grid search (Wind pivot Faz 2 Prompt 5).

Tek asset uzerinde AutoencoderAnomalyEngine'in 4 hyperparameter ekseninde
sweep'i; her kombinasyon icin asset'in CARE dataset'leri uzerinde CARE
skoru hesaplanir, top-N rapor edilir.

Strateji:
* Asset bazli grid (default Asset 0 — Event 0 imzasi Gen bearing failure).
* 4 eksen:
    - hidden_layer_sizes: (32,8,32) [baseline], (32,16,32), (64,16,64)
    - activation: 'relu', 'tanh'
    - alpha (L2): 0.0001, 0.001
    - max_iter: 300, 500
  → 3 × 2 × 2 × 2 = 24 kombinasyon (tum cross product 54'un alt-kumesi).
* Her kombinasyon: training (Asset 0'in 5 event'inin 'train' partition'lari) →
  asset'e ait 5 dataset uzerinde AE prediction → CARE skoru.
* Top-5 (CARE descending) rapor edilir; tum sonuclar JSON'a yazilir.

Calistirma::

    set -a; source _personal/wind_pivot/.env.wind; set +a
    PYTHONPATH=src .venv/bin/python scripts/ae_grid_search.py \\
        --event-info  "_personal/wind_pivot/raw/CARE_To_Compare/Wind Farm A/event_info.csv" \\
        --datasets-dir "_personal/wind_pivot/raw/CARE_To_Compare/Wind Farm A/datasets" \\
        --asset 0 \\
        --report-path _personal/wind_pivot/reports/07_grid_search_asset0.md \\
        --json-path   _personal/wind_pivot/reports/07_grid_search_asset0.json \\
        --per-event

Cikti:
* Markdown raporu (top-5 + parametreler + training time).
* JSON dump (tum 24 kombinasyon + CARE detaylari).
"""

from __future__ import annotations

import argparse
import itertools
import json
import logging
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from custos.analytics.autoencoder_engine import (
    DEFAULT_ALPHA,
    AutoencoderAnomalyEngine,
)
from custos.analytics.care_scorer import CAREResult, CAREScorer, Event

# train_wind_models_from_csv.py'deki training pipeline'i yeniden kullaniyoruz
# (duplicate kod degil) — collect_training_rows + meta sabitler.
sys.path.insert(0, str(Path(__file__).resolve().parent))
# ruff: noqa: E402
from train_wind_models_from_csv import (
    WEEK_TICKS_10MIN_SCADA,
    collect_training_rows,
    load_event_info_assets,
    load_event_info_full,
)

if TYPE_CHECKING:
    from numpy.typing import NDArray

logger = logging.getLogger("ae_grid_search")

# Default grid — 12 kombinasyon (3 × 2 × 2 × 1). Tum cross product 54
# olur (3×3×3×2); alt kume secimi paper-a uygun:
# - max_iter sweep'i kaldirildi (early_stopping=True + n_iter_no_change=25
#   convergence'i yonetir; sabit 500 yeterli);
# - alpha 0.01 atildi (overfitting baskilamasi cok agresif olabilir);
# - architecture + activation + alpha eksenleri korunarak hizli tarama.
# 12 kombinasyon ile spec'in 16-24 alt sinirinin altinda kaliyor ama bilgi
# yogunlugu acisindan yeterli (raporlanir).
DEFAULT_HIDDEN_SIZES: tuple[tuple[int, ...], ...] = (
    (32, 8, 32),    # paper baseline
    (32, 16, 32),   # bottleneck 8 → 16
    (64, 16, 64),   # daha genis encoder
)
DEFAULT_ACTIVATIONS: tuple[str, ...] = ("relu", "tanh")
DEFAULT_ALPHAS: tuple[float, ...] = (0.0001, 0.001)
DEFAULT_MAX_ITERS: tuple[int, ...] = (500,)

# Asset 0 CSV dataset'lerinin column structure — features collection icin.
# train_wind_models_from_csv.META_COLS ile uyumlu.

# CARE Scorer default'lari (validate_models_on_care.py ile uyumlu).
CARE_TC = 72
CARE_BETA = 0.5


@dataclass
class GridResult:
    """Bir kombinasyonun sonucu — CARE + meta + timing."""

    hidden_layer_sizes: tuple[int, ...]
    activation: str
    alpha: float
    max_iter: int
    care: float
    coverage: float
    accuracy: float
    reliability: float
    earliness: float
    n_train_samples: int
    n_test_samples: int
    train_time_sec: float
    ae_threshold: float

    def to_dict(self) -> dict[str, object]:
        d = asdict(self)
        d["hidden_layer_sizes"] = list(self.hidden_layer_sizes)
        return d


def _safe_float(value: str | None) -> float:
    """train_wind_models_from_csv.py ile birebir uyumlu — local copy."""
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
    if value is None:
        return 0
    s = value.strip()
    if not s:
        return 0
    try:
        return int(s)
    except ValueError:
        return 0


def collect_asset_training_matrix(
    asset: int,
    event_ids: list[int],
    datasets_dir: Path,
    *,
    per_event: bool,
    event_info_full: dict[int, dict[str, int]] | None,
    exclude_week_ticks: int,
) -> NDArray[np.float64]:
    """Asset icin tum event CSV'lerinin training partition'ini birlestirir.

    train_wind_models_from_csv.train_asset_models'in ilk yarisini eden
    kisim — model egitimi olmadan sadece feature matrix donduruyoruz.
    """
    all_features: list[NDArray[np.float64]] = []
    sensor_cols_ref: list[str] = []
    for ev_id in event_ids:
        csv_path = datasets_dir / f"{ev_id}.csv"
        if per_event and event_info_full is not None:
            ev_meta = event_info_full.get(ev_id, {})
            start_id = ev_meta.get("event_start_id")
            ev_label = ev_meta.get("event_label")
            cols, features = collect_training_rows(
                csv_path,
                event_start_id=int(start_id) if start_id is not None else None,
                event_label=str(ev_label) if ev_label is not None else None,
                exclude_week_ticks=exclude_week_ticks,
            )
        else:
            cols, features = collect_training_rows(csv_path)
        if features.size == 0:
            continue
        if not sensor_cols_ref:
            sensor_cols_ref = cols
        elif cols != sensor_cols_ref:
            continue
        all_features.append(features)
    if not all_features:
        return np.zeros((0, 0), dtype=np.float64)
    return np.concatenate(all_features, axis=0)


def load_asset_test_datasets(
    asset: int,
    event_info_full: dict[int, dict[str, int]],
    datasets_dir: Path,
) -> list[tuple[int, NDArray[np.float64], NDArray[np.int_], str, int]]:
    """Asset'e ait tum event CSV'lerini test seti olarak yukler.

    Donus: ``[(event_id, features, status, label, event_start_id), ...]``.
    Validate scriptiyle benzer veri yapisi; tum satirlari (train+test+prediction
    window) korur cunku CARE skoru tum dataset uzerinde hesaplanir.
    """
    from train_wind_models_from_csv import META_COLS  # noqa: PLC0415

    out: list[tuple[int, NDArray[np.float64], NDArray[np.int_], str, int]] = []
    for ev_id, meta in event_info_full.items():
        if meta.get("asset") != asset:
            continue
        csv_path = datasets_dir / f"{ev_id}.csv"
        if not csv_path.exists():
            continue
        # CSV'yi tum satirlariyla yukle (train+test+ prediction window dahil)
        import csv as _csv  # noqa: PLC0415

        sensor_cols: list[str] = []
        rows: list[list[float]] = []
        statuses: list[int] = []
        with csv_path.open(encoding="utf-8") as f:
            reader = _csv.DictReader(f, delimiter=";")
            sensor_cols = [
                c for c in (reader.fieldnames or [])
                if c not in META_COLS
            ]
            for row in reader:
                rows.append([_safe_float(row.get(c, "")) for c in sensor_cols])
                statuses.append(_safe_int(row.get("status_type_id")))
        if not rows:
            continue
        features = np.asarray(rows, dtype=np.float64)
        status = np.asarray(statuses, dtype=np.int_)
        label = str(meta.get("event_label", "normal"))
        start_id = int(meta.get("event_start_id", 0))
        out.append((ev_id, features, status, label, start_id))
    return out


def evaluate_combo(
    train_matrix: NDArray[np.float64],
    test_datasets: list[tuple[int, NDArray[np.float64], NDArray[np.int_], str, int]],
    *,
    hidden_layer_sizes: tuple[int, ...],
    activation: str,
    alpha: float,
    max_iter: int,
    n_iter_no_change: int,
    random_state: int,
) -> tuple[CAREResult | None, AutoencoderAnomalyEngine, float]:
    """Bir kombinasyonu egit, asset'in test dataset'lerinde CARE skoru hesapla."""
    t0 = time.perf_counter()
    engine = AutoencoderAnomalyEngine(
        hidden_layer_sizes=hidden_layer_sizes,
        activation=activation,
        alpha=alpha,
        max_iter=max_iter,
        n_iter_no_change=n_iter_no_change,
        random_state=random_state,
        early_stopping=True,
    )
    engine.train(train_matrix, status_types=None)
    train_time = time.perf_counter() - t0

    # Test predictions — asset'e ait tum dataset'leri concat et, Event listesi olustur.
    all_preds: list[NDArray[np.int_]] = []
    all_status: list[NDArray[np.int_]] = []
    events: list[Event] = []
    cursor = 0
    for ev_id, features, status, label, start_id in test_datasets:
        if features.shape[1] != engine.n_features:
            logger.warning(
                "Event %d feature sayisi uyumsuz: %d vs egitim %d — skip",
                ev_id, features.shape[1], engine.n_features,
            )
            continue
        preds = engine.is_anomaly(features).astype(np.int_)
        all_preds.append(preds)
        all_status.append(status)
        n_rows = preds.shape[0]
        ds_start = cursor
        ds_end = cursor + n_rows - 1
        if label == "anomaly":
            event_start = min(ds_start + start_id, ds_end)
            event_start = max(event_start, ds_start)
            events.append(
                Event(
                    event_id=ev_id,
                    label="anomaly",
                    event_start_id=event_start,
                    event_end_id=ds_end,
                    dataset_start_id=ds_start,
                    dataset_end_id=ds_end,
                ),
            )
        else:
            events.append(
                Event(
                    event_id=ev_id,
                    label="normal",
                    event_start_id=ds_start,
                    event_end_id=ds_end,
                ),
            )
        cursor += n_rows

    if not all_preds:
        return None, engine, train_time

    preds_concat = np.concatenate(all_preds)
    status_concat = np.concatenate(all_status)
    scorer = CAREScorer(tc=CARE_TC, beta=CARE_BETA)
    result = scorer.score(preds_concat, events, status_types=status_concat)
    return result, engine, train_time


def run(args: argparse.Namespace) -> int:
    """Grid sweep orchestrator."""
    event_info_path = Path(args.event_info)
    datasets_dir = Path(args.datasets_dir)
    if not event_info_path.exists():
        logger.error("event_info.csv bulunamadi: %s", event_info_path)
        return 2
    if not datasets_dir.exists():
        logger.error("datasets dizini bulunamadi: %s", datasets_dir)
        return 2

    asset_events = load_event_info_assets(event_info_path)
    if args.asset not in asset_events:
        logger.error(
            "Asset %d event_info'da yok. Mevcut: %s",
            args.asset, sorted(asset_events.keys()),
        )
        return 1
    event_ids = asset_events[args.asset]

    event_info_full = load_event_info_full(event_info_path)
    test_datasets = load_asset_test_datasets(args.asset, event_info_full, datasets_dir)
    if not test_datasets:
        logger.error("Asset %d icin test dataset yuklenemedi", args.asset)
        return 1

    train_matrix = collect_asset_training_matrix(
        args.asset,
        event_ids,
        datasets_dir,
        per_event=args.per_event,
        event_info_full=event_info_full,
        exclude_week_ticks=args.exclude_week_ticks,
    )
    if train_matrix.size == 0:
        logger.error("Asset %d training matrix bos", args.asset)
        return 1

    logger.info(
        "Asset %d hazir: train=%d × %d, test_datasets=%d (per_event=%s)",
        args.asset, train_matrix.shape[0], train_matrix.shape[1],
        len(test_datasets), args.per_event,
    )

    # Grid kombinasyonlari
    hidden_sizes = DEFAULT_HIDDEN_SIZES
    activations = DEFAULT_ACTIVATIONS
    alphas = DEFAULT_ALPHAS
    max_iters = DEFAULT_MAX_ITERS
    combos = list(itertools.product(hidden_sizes, activations, alphas, max_iters))
    logger.info("Grid: %d kombinasyon", len(combos))

    results: list[GridResult] = []
    for idx, (hidden, act, alpha, mi) in enumerate(combos, start=1):
        logger.info(
            "[%d/%d] hidden=%s activation=%s alpha=%s max_iter=%d",
            idx, len(combos), hidden, act, alpha, mi,
        )
        try:
            care, engine, train_time = evaluate_combo(
                train_matrix,
                test_datasets,
                hidden_layer_sizes=hidden,
                activation=act,
                alpha=alpha,
                max_iter=mi,
                n_iter_no_change=args.n_iter_no_change,
                random_state=args.random_state,
            )
        except Exception as exc:  # noqa: BLE001 — gridi devam ettir
            logger.warning("Kombinasyon hatasi: %s", exc)
            continue
        if care is None:
            continue
        # Test setinin toplam satir sayisi
        n_test = sum(d[1].shape[0] for d in test_datasets)
        results.append(
            GridResult(
                hidden_layer_sizes=hidden,
                activation=act,
                alpha=alpha,
                max_iter=mi,
                care=care.final,
                coverage=care.coverage,
                accuracy=care.accuracy,
                reliability=care.reliability,
                earliness=care.earliness,
                n_train_samples=engine.n_train_samples,
                n_test_samples=n_test,
                train_time_sec=train_time,
                ae_threshold=engine.threshold,
            ),
        )
        logger.info(
            "  → CARE=%.4f earliness=%.3f train_time=%.2fs",
            care.final, care.earliness, train_time,
        )

    if not results:
        logger.error("Hicbir kombinasyon basarili olamadi")
        return 1

    results.sort(key=lambda r: r.care, reverse=True)

    # Rapor (markdown)
    if args.report_path:
        report_path = Path(args.report_path)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            f"# AE Grid Search — Asset {args.asset}",
            "",
            f"Wind pivot Faz 2 Prompt 5 (per_event={args.per_event})",
            "",
            f"- Toplam kombinasyon: **{len(combos)}**",
            f"- Basarili kombinasyon: **{len(results)}**",
            f"- Asset {args.asset} training rows: **{train_matrix.shape[0]:,}**",
            f"- Asset {args.asset} test datasets: **{len(test_datasets)}**",
            "",
            "## Top-5 (CARE descending)",
            "",
            (
                "| Rank | hidden | activation | alpha | max_iter | CARE | "
                "Earliness | Acc | Cov | Rel | train_s |"
            ),
            "|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for rank, r in enumerate(results[:5], start=1):
            lines.append(
                f"| {rank} | `{r.hidden_layer_sizes}` | `{r.activation}` | "
                f"{r.alpha} | {r.max_iter} | **{r.care:.4f}** | "
                f"{r.earliness:.3f} | {r.accuracy:.3f} | {r.coverage:.3f} | "
                f"{r.reliability:.3f} | {r.train_time_sec:.1f} |",
            )
        lines.extend(["", "## Tum Sonuclar (sirali)", "",
                      "| hidden | activation | alpha | max_iter | CARE |",
                      "|---|---|---:|---:|---:|"])
        for r in results:
            lines.append(
                f"| `{r.hidden_layer_sizes}` | `{r.activation}` | "
                f"{r.alpha} | {r.max_iter} | {r.care:.4f} |",
            )
        lines.append("")
        report_path.write_text("\n".join(lines), encoding="utf-8")
        logger.info("Markdown rapor yazildi: %s", report_path)

    # JSON dump (analiz icin)
    if args.json_path:
        json_path = Path(args.json_path)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(
            json.dumps([r.to_dict() for r in results], indent=2),
            encoding="utf-8",
        )
        logger.info("JSON sonuc yazildi: %s", json_path)

    # Stdout top-5 ozet
    print("\n=== Top-5 ===")  # noqa: T201
    for rank, r in enumerate(results[:5], start=1):
        print(  # noqa: T201
            f"{rank}. CARE={r.care:.4f} | hidden={r.hidden_layer_sizes} "
            f"activation={r.activation} alpha={r.alpha} max_iter={r.max_iter} "
            f"train_time={r.train_time_sec:.1f}s",
        )
    return 0


def build_argparser() -> argparse.ArgumentParser:
    """CLI argumanlari."""
    parser = argparse.ArgumentParser(
        description=(
            "AE hyperparameter grid search (Wind pivot Faz 2 Prompt 5). "
            "Tek asset uzerinde 24 kombinasyon, asset-bazli CARE skoru, "
            "top-5 rapor."
        ),
    )
    parser.add_argument("--event-info", required=True, help="event_info.csv yolu")
    parser.add_argument(
        "--datasets-dir", required=True,
        help="<event_id>.csv dosyalarinin oldugu dizin",
    )
    parser.add_argument(
        "--asset", type=int, default=0,
        help="Grid'in calistirilacagi Fraunhofer asset ID (default 0)",
    )
    parser.add_argument(
        "--per-event", action="store_true",
        help=(
            "event_start_id - exclude_week_ticks oncesini training'ten cikar "
            "(fault yaklasimi sinyali sizmasin)."
        ),
    )
    parser.add_argument(
        "--exclude-week-ticks", type=int, default=WEEK_TICKS_10MIN_SCADA,
        help=f"Per-event exclude window (default {WEEK_TICKS_10MIN_SCADA}).",
    )
    parser.add_argument(
        "--n-iter-no-change", type=int, default=25,
        help="AE early stopping patience (sabit grid icin, default 25).",
    )
    parser.add_argument(
        "--random-state", type=int, default=42,
        help="Reproducibility seed (default 42).",
    )
    parser.add_argument(
        "--report-path", default=None,
        help="Markdown top-5 rapor yolu (opsiyonel).",
    )
    parser.add_argument(
        "--json-path", default=None,
        help="Tum sonuclarin JSON dump'i (opsiyonel).",
    )
    _ = DEFAULT_ALPHA  # CLI override degil — grid icinde sabit kume.
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
