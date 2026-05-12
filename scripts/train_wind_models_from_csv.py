"""CARE Wind Farm A CSV'lerinden dogrudan IF + AE modeli egitir (Faz 1.5).

Mevcut ``train_autoencoder_models.py`` ve ``train_anomaly_models.py`` DB'den
veri okur (collector + replay simulator + asyncpg pipeline gerektirir). Faz
1.5 kapanis denemesi icin daha hizli/yalitik bir yol gerekiyordu: bu script
Fraunhofer CSV'lerini dogrudan numpy matrisine yukler, asset bazinda hem
Isolation Forest hem sklearn Autoencoder modeli egitir, joblib'a yazar.

Wind pivot Faz 2 Prompt 5 (2026-05-12) ile genisletildi:
- AE hyperparameter CLI pass-through (``--alpha``, ``--activation``,
  ``--n-iter-no-change``, ``--max-iter``, ``--hidden-layer-sizes``).
- ``--per-event`` flag: her event_info satiri icin event_start_id'den 1 hafta
  oncesini training'ten cikar (model "fault yaklasimi" sinyalini ogrenmesin).
  Custos per-asset model kullandigi icin asset basina HALA tek model uretilir;
  sadece training window event-aware. Asset X'in N event'i varsa N×window
  birlestirilir.

Kullanim::

    .venv/bin/python scripts/train_wind_models_from_csv.py \\
        --datasets-dir _personal/wind_pivot/raw/CARE_To_Compare/Wind\\ Farm\\ A/datasets \\
        --event-info  _personal/wind_pivot/raw/CARE_To_Compare/Wind\\ Farm\\ A/event_info.csv \\
        --models-dir  data/models \\
        --assets 0,10,11,13,21 \\
        --per-event --hidden-layer-sizes 32,16,32 --alpha 0.001 --max-iter 500

Egitim kurali (paper Section 3 + ML kurallari, CLAUDE.md):
- Sadece ``train_test == 'train'`` satirlar.
- Sadece ``status_type_id ∈ {0=Normal, 2=Idling}`` satirlar.
- NaN/inf 0.0 ile doldurulur (AE NaN kabul etmez).
- Asset basina TUM CSV'lerin training partisyonu birleserek tek model uretir.
- ``--per-event``: ek olarak ``row_id < event_start_id - WEEK_TICKS``
  kosulu (WEEK_TICKS=1008 = 7 gun × 24 saat × 6 tick/saat @ 10 dk SCADA).
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from custos.analytics.autoencoder_engine import (
    DEFAULT_ACTIVATION,
    DEFAULT_ALPHA,
    DEFAULT_N_ITER_NO_CHANGE,
    DEFAULT_VALID_STATUS_TYPES,
    AutoencoderAnomalyEngine,
)

if TYPE_CHECKING:
    from numpy.typing import NDArray

logger = logging.getLogger("train_wind_csv")

# Sabit metadata kolon adlari (Fraunhofer CARE Wind Farm A semasi).
META_COLS = {"time_stamp", "asset_id", "id", "train_test", "status_type_id"}

# IF modelleri ``anomaly_<asset>.joblib`` adi ile yazilir (mevcut AVM
# pattern'i ile uyumlu).
IF_FILENAME_TEMPLATE = "anomaly_{asset}.joblib"

# AE modelleri ``autoencoder_<asset>_wind.joblib`` adi ile yazilir
# (validate_models_on_care.py beklentisi).
AE_FILENAME_TEMPLATE = "autoencoder_{asset}_wind.joblib"

# IsolationForest contamination — paper baseline 0.05 (top %5 anomaly).
IF_CONTAMINATION = 0.05
IF_N_ESTIMATORS = 100
IF_RANDOM_STATE = 42

# Wind pivot Faz 2 Prompt 5 — per-event mode exclude window.
# 10 dakika SCADA aralig varsayimi (Fraunhofer Wind Farm A): 1 hafta =
# 7 gun × 24 saat × 6 tick/saat = 1008 tick. event_start_id - WEEK_TICKS
# oncesi training'e dahil edilir, sonrasi exclude (model "fault yaklasimi"
# sinyalini ogrenmesin).
WEEK_TICKS_10MIN_SCADA = 1008


def _safe_float(value: str | None) -> float:
    """CSV float parse; bos / 'nan' / hata → 0.0."""
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
    """CSV int parse; bos / hata → 0 (Normal status)."""
    if value is None:
        return 0
    s = value.strip()
    if not s:
        return 0
    try:
        return int(s)
    except ValueError:
        return 0


def load_event_info_assets(event_info: Path) -> dict[int, list[int]]:
    """event_info.csv → ``{asset_id: [event_id, ...]}`` dict.

    Asset basina hangi CSV'lerin oldugunu bulur. Asset id ``int``e cevrilemezse
    skipping (CSV satiri bozuk olabilir).
    """
    asset_events: dict[int, list[int]] = defaultdict(list)
    with event_info.open(encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            try:
                asset = int(row["asset"])
                ev_id = int(row["event_id"])
            except (KeyError, ValueError):
                continue
            asset_events[asset].append(ev_id)
    return dict(asset_events)


def load_event_info_full(event_info: Path) -> dict[int, dict[str, int]]:
    """event_info.csv → ``{event_id: {"asset": int, "event_start_id": int,
    "event_label": "anomaly"|"normal"}}``.

    Wind pivot Faz 2 Prompt 5 — per-event training window icin
    ``event_start_id`` gerekiyor. ``event_label`` sadece ``anomaly``
    satirlari icin exclude uygulanir; ``normal`` dataset'lerde tum
    train satirlari korunur.
    """
    out: dict[int, dict[str, int]] = {}
    with event_info.open(encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            try:
                ev_id = int(row["event_id"])
                asset = int(row["asset"])
                start_id = int(row["event_start_id"])
            except (KeyError, ValueError):
                continue
            out[ev_id] = {
                "asset": asset,
                "event_start_id": start_id,
                "event_label": row.get("event_label", "").strip(),  # type: ignore[dict-item]
            }
    return out


def collect_training_rows(
    csv_path: Path,
    *,
    event_start_id: int | None = None,
    event_label: str | None = None,
    exclude_week_ticks: int = WEEK_TICKS_10MIN_SCADA,
) -> tuple[list[str], NDArray[np.float64]]:
    """Bir CSV'den sadece train + status ∈ {0,2} satirlari yukler.

    Donus: (sensor_cols, features). features shape (n_train_rows, n_sensors).
    Hatali/eksik dosya → boş feature.

    Wind pivot Faz 2 Prompt 5 — per-event mode:
    - ``event_start_id`` verilmis VE ``event_label == 'anomaly'`` ise
      CSV'deki satir indeksi ``row_idx < event_start_id - exclude_week_ticks``
      kosulu uygulanir (fault yaklasimi sinyali training'e sizmaz).
    - ``event_label == 'normal'`` veya ``event_start_id is None`` ise mevcut
      davranis (status + train_test filtreleri yeterli).
    """
    if not csv_path.exists():
        logger.warning("CSV bulunamadi (skip): %s", csv_path)
        return ([], np.zeros((0, 0), dtype=np.float64))

    sensor_cols: list[str] = []
    rows_filtered: list[list[float]] = []
    apply_event_exclude = (
        event_start_id is not None and event_label == "anomaly"
    )
    cutoff_id = (
        int(event_start_id) - exclude_week_ticks
        if event_start_id is not None
        else 0
    )
    with csv_path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter=";")
        sensor_cols = [
            c for c in (reader.fieldnames or [])
            if c not in META_COLS
        ]
        valid_set = set(DEFAULT_VALID_STATUS_TYPES)
        for row in reader:
            if row.get("train_test", "").strip() != "train":
                continue
            if _safe_int(row.get("status_type_id")) not in valid_set:
                continue
            if apply_event_exclude:
                row_idx = _safe_int(row.get("id"))
                if row_idx >= cutoff_id:
                    continue
            rows_filtered.append([_safe_float(row.get(c, "")) for c in sensor_cols])
    if not rows_filtered:
        return (sensor_cols, np.zeros((0, len(sensor_cols)), dtype=np.float64))
    return (sensor_cols, np.asarray(rows_filtered, dtype=np.float64))


def train_asset_models(
    asset: int,
    event_ids: list[int],
    datasets_dir: Path,
    models_dir: Path,
    *,
    ae_hyperparams: dict[str, object] | None = None,
    per_event: bool = False,
    event_info_full: dict[int, dict[str, int]] | None = None,
    exclude_week_ticks: int = WEEK_TICKS_10MIN_SCADA,
    train_if: bool = True,
    train_ae: bool = True,
) -> dict[str, object]:
    """Bir asset icin IF + AE egitir, joblib'a yazar.

    Faz 2 Prompt 5:
    - ``ae_hyperparams``: AutoencoderAnomalyEngine'e gecirilen kwargs
      (hidden_layer_sizes, max_iter, alpha, activation, n_iter_no_change).
      None ise modul default'larina duser.
    - ``per_event``: True ise her event_id icin training window
      ``event_start_id - exclude_week_ticks`` ile clip edilir; normal
      dataset'lerde uygulanmaz. Asset hala TEK model uretir (window'lar
      birlestirilir).
    - ``train_if`` / ``train_ae``: tek tek devre disi birakilabilir
      (grid search'te AE'ye odaklanip IF retrain'i atlayabiliriz).

    Donus: ``{"if_path": Path|None, "ae_path": Path|None, "n_train": int,
    "n_features": int, "ae_threshold": float|None}``. Hata durumunda ilgili
    path None.
    """
    ae_kwargs = dict(ae_hyperparams) if ae_hyperparams else {}
    # 1) Tum event CSV'lerinin training satirlarini birlestir
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
            logger.warning(
                "Asset %d: kolon yapisi farkli (event %d) — skip",
                asset, ev_id,
            )
            continue
        all_features.append(features)

    if not all_features:
        logger.warning("Asset %d: training verisi yok — egitim atlandi", asset)
        return {"if_path": None, "ae_path": None, "n_train": 0, "n_features": 0,
                "ae_threshold": None}

    train_matrix: NDArray[np.float64] = np.concatenate(all_features, axis=0)
    n_train, n_features = train_matrix.shape
    logger.info(
        "Asset %d training matrix: %d satir × %d feature (events=%s, per_event=%s)",
        asset, n_train, n_features, event_ids, per_event,
    )

    models_dir.mkdir(parents=True, exist_ok=True)
    import joblib  # noqa: PLC0415

    if_path: Path | None = None
    if train_if:
        # 2) Isolation Forest egitimi
        from sklearn.ensemble import IsolationForest  # noqa: PLC0415

        if_model = IsolationForest(
            n_estimators=IF_N_ESTIMATORS,
            contamination=IF_CONTAMINATION,
            random_state=IF_RANDOM_STATE,
            n_jobs=-1,
        )
        if_model.fit(train_matrix)
        if_path = models_dir / IF_FILENAME_TEMPLATE.format(asset=asset)
        joblib.dump(if_model, if_path)
        logger.info("IF model kaydedildi: %s", if_path)

    ae_path: Path | None = None
    ae_threshold: float | None = None
    if train_ae:
        # 3) Autoencoder egitimi — status filter zaten uygulandi (status_types=None).
        # Faz 2 P5: hyperparameter dict CLI'dan / grid search'ten gelir.
        ae = AutoencoderAnomalyEngine(**ae_kwargs)  # type: ignore[arg-type]
        ae.train(train_matrix, status_types=None)
        ae_path = models_dir / AE_FILENAME_TEMPLATE.format(asset=asset)
        ae.save(ae_path)
        ae_threshold = ae.threshold
        logger.info(
            "AE model kaydedildi: %s (threshold=%.4f, hp=%s)",
            ae_path, ae_threshold, ae_kwargs,
        )

    return {
        "if_path": if_path,
        "ae_path": ae_path,
        "n_train": n_train,
        "n_features": n_features,
        "ae_threshold": ae_threshold,
    }


def _parse_hidden_layer_sizes(spec: str) -> tuple[int, ...]:
    """``"32,16,32"`` → ``(32, 16, 32)``. Negatif/0 hatalidir."""
    parts = [p.strip() for p in spec.split(",") if p.strip()]
    sizes = tuple(int(p) for p in parts)
    if not sizes or any(s <= 0 for s in sizes):
        msg = f"hidden_layer_sizes gecersiz: {spec!r}"
        raise ValueError(msg)
    return sizes


def run(args: argparse.Namespace) -> int:
    """Asset listesi uzerinde egitim kostur, ozet bas."""
    event_info = Path(args.event_info)
    datasets_dir = Path(args.datasets_dir)
    models_dir = Path(args.models_dir)

    if not event_info.exists():
        logger.error("event_info.csv bulunamadi: %s", event_info)
        return 2
    if not datasets_dir.exists():
        logger.error("datasets dizini bulunamadi: %s", datasets_dir)
        return 2

    asset_events = load_event_info_assets(event_info)
    if args.assets:
        wanted = {int(a) for a in args.assets.split(",")}
        asset_events = {a: evs for a, evs in asset_events.items() if a in wanted}
    if not asset_events:
        logger.error("Egitilecek asset yok (filtreli sonuc bos)")
        return 1

    # Faz 2 P5 — AE hyperparameter dict (CLI'dan).
    ae_hyperparams: dict[str, object] = {
        "hidden_layer_sizes": _parse_hidden_layer_sizes(args.hidden_layer_sizes),
        "max_iter": args.max_iter,
        "alpha": args.alpha,
        "activation": args.activation,
        "n_iter_no_change": args.n_iter_no_change,
    }

    # per-event mode icin full event_info gerekli (event_start_id + label).
    event_info_full = load_event_info_full(event_info) if args.per_event else None

    print(  # noqa: T201
        f"Egitilecek asset sayisi: {len(asset_events)} "
        f"(per_event={args.per_event}, ae_hp={ae_hyperparams})",
    )
    summary: list[dict[str, object]] = []
    for asset, ev_list in sorted(asset_events.items()):
        result = train_asset_models(
            asset,
            ev_list,
            datasets_dir,
            models_dir,
            ae_hyperparams=ae_hyperparams,
            per_event=args.per_event,
            event_info_full=event_info_full,
            exclude_week_ticks=args.exclude_week_ticks,
            train_if=not args.skip_if,
            train_ae=not args.skip_ae,
        )
        result["asset"] = asset
        summary.append(result)

    # Ozet tablo
    print("\n=== Ozet ===")  # noqa: T201
    print(f"{'asset':>6} | {'n_train':>10} | {'n_feat':>6} | {'ae_thr':>8} | "  # noqa: T201
          f"if_path | ae_path")
    for r in summary:
        if_p = "OK" if r["if_path"] else "SKIP"
        ae_p = "OK" if r["ae_path"] else "SKIP"
        thr = r["ae_threshold"]
        thr_str = f"{thr:.4f}" if thr is not None else "—"
        print(  # noqa: T201
            f"{r['asset']:>6} | {r['n_train']:>10} | {r['n_features']:>6} | "
            f"{thr_str:>8} | {if_p:>7} | {ae_p:>7}",
        )
    # Skip secimi var iken if_path/ae_path None'i hata sayma.
    failed = 0
    for r in summary:
        if not args.skip_if and r["if_path"] is None:
            failed += 1
        if not args.skip_ae and r["ae_path"] is None:
            failed += 1
    return 0 if failed == 0 else 1


def build_argparser() -> argparse.ArgumentParser:
    """CLI argumanlari."""
    parser = argparse.ArgumentParser(
        description=(
            "Fraunhofer CARE Wind Farm A CSV'lerinden dogrudan asset bazinda "
            "Isolation Forest + sklearn Autoencoder modeli egitir. "
            "Faz 2 P5: AE hyperparameter CLI + per-event training window."
        ),
    )
    parser.add_argument(
        "--event-info",
        required=True,
        help="event_info.csv yolu (Fraunhofer Wind Farm A formatinda)",
    )
    parser.add_argument(
        "--datasets-dir",
        required=True,
        help="<event_id>.csv dosyalarinin oldugu dizin",
    )
    parser.add_argument(
        "--models-dir",
        default="data/models",
        help="Cikti joblib dosyalarinin dizini (default data/models)",
    )
    parser.add_argument(
        "--assets",
        default=None,
        help="Egitilecek asset id listesi (virgul ayirici). Verilmezse tumu.",
    )
    # Faz 2 P5 — AE hyperparameter CLI args.
    parser.add_argument(
        "--hidden-layer-sizes",
        default="32,8,32",
        help=(
            "AE hidden katmanlar (virgul ayirici). Default '32,8,32' "
            "(paper baseline). Faz 2 P5 grid'i '32,16,32' / '64,16,64' da denenir."
        ),
    )
    parser.add_argument(
        "--max-iter",
        type=int,
        default=200,
        help="AE max_iter (default 200). Faz 2 P5 grid: 300 / 500 / 800.",
    )
    parser.add_argument(
        "--alpha",
        type=float,
        default=DEFAULT_ALPHA,
        help=(
            f"AE L2 regularization (sklearn alpha). Default {DEFAULT_ALPHA}. "
            f"Faz 2 P5 grid: 0.0001 / 0.001 / 0.01."
        ),
    )
    parser.add_argument(
        "--activation",
        default=DEFAULT_ACTIVATION,
        choices=["relu", "tanh", "logistic", "identity"],
        help=(
            f"AE hidden katman aktivasyon. Default '{DEFAULT_ACTIVATION}'. "
            f"Faz 2 P5 grid: 'relu' / 'tanh'."
        ),
    )
    parser.add_argument(
        "--n-iter-no-change",
        type=int,
        default=DEFAULT_N_ITER_NO_CHANGE,
        help=(
            f"AE early stopping patience. Default {DEFAULT_N_ITER_NO_CHANGE} "
            f"(sklearn default 10'dan artirildi)."
        ),
    )
    # Faz 2 P5 — Per-event training window flag.
    parser.add_argument(
        "--per-event",
        action="store_true",
        help=(
            "Anomaly event'lerinde event_start_id - exclude_week_ticks "
            "oncesini training'ten cikar (fault yaklasimi sinyali sizmasin)."
        ),
    )
    parser.add_argument(
        "--exclude-week-ticks",
        type=int,
        default=WEEK_TICKS_10MIN_SCADA,
        help=(
            f"Per-event mode exclude window (tick). Default "
            f"{WEEK_TICKS_10MIN_SCADA} (1 hafta @ 10 dk SCADA)."
        ),
    )
    # Selective skip — grid search'te IF retrain'i atlamak icin.
    parser.add_argument(
        "--skip-if",
        action="store_true",
        help="Sadece AE egit, IF retrain'i atla (grid search / iterasyon).",
    )
    parser.add_argument(
        "--skip-ae",
        action="store_true",
        help="Sadece IF egit, AE retrain'i atla.",
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
