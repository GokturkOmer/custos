"""CARE Wind Farm A CSV'lerinden dogrudan IF + AE modeli egitir (Faz 1.5).

Mevcut ``train_autoencoder_models.py`` ve ``train_anomaly_models.py`` DB'den
veri okur (collector + replay simulator + asyncpg pipeline gerektirir). Faz
1.5 kapanis denemesi icin daha hizli/yalitik bir yol gerekiyordu: bu script
Fraunhofer CSV'lerini dogrudan numpy matrisine yukler, asset bazinda hem
Isolation Forest hem sklearn Autoencoder modeli egitir, joblib'a yazar.

Kullanim::

    .venv/bin/python scripts/train_wind_models_from_csv.py \\
        --datasets-dir _personal/wind_pivot/raw/CARE_To_Compare/Wind\\ Farm\\ A/datasets \\
        --event-info  _personal/wind_pivot/raw/CARE_To_Compare/Wind\\ Farm\\ A/event_info.csv \\
        --models-dir  data/models \\
        --assets 0,10,11,13,21

Egitim kurali (paper Section 3 + ML kurallari, CLAUDE.md):
- Sadece ``train_test == 'train'`` satirlar.
- Sadece ``status_type_id ∈ {0=Normal, 2=Idling}`` satirlar.
- NaN/inf 0.0 ile doldurulur (AE NaN kabul etmez).
- Asset basina TUM CSV'lerin training partisyonu birleserek tek model uretir.
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


def collect_training_rows(
    csv_path: Path,
) -> tuple[list[str], NDArray[np.float64]]:
    """Bir CSV'den sadece train + status ∈ {0,2} satirlari yukler.

    Donus: (sensor_cols, features). features shape (n_train_rows, n_sensors).
    Hatali/eksik dosya → boş feature.
    """
    if not csv_path.exists():
        logger.warning("CSV bulunamadi (skip): %s", csv_path)
        return ([], np.zeros((0, 0), dtype=np.float64))

    sensor_cols: list[str] = []
    rows_filtered: list[list[float]] = []
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
            rows_filtered.append([_safe_float(row.get(c, "")) for c in sensor_cols])
    if not rows_filtered:
        return (sensor_cols, np.zeros((0, len(sensor_cols)), dtype=np.float64))
    return (sensor_cols, np.asarray(rows_filtered, dtype=np.float64))


def train_asset_models(
    asset: int,
    event_ids: list[int],
    datasets_dir: Path,
    models_dir: Path,
) -> dict[str, object]:
    """Bir asset icin IF + AE egitir, joblib'a yazar.

    Donus: ``{"if_path": Path|None, "ae_path": Path|None, "n_train": int,
    "n_features": int, "ae_threshold": float|None}``. Hata durumunda ilgili
    path None.
    """
    # 1) Tum event CSV'lerinin training satirlarini birlestir
    all_features: list[NDArray[np.float64]] = []
    sensor_cols_ref: list[str] = []
    for ev_id in event_ids:
        csv_path = datasets_dir / f"{ev_id}.csv"
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
        "Asset %d training matrix: %d satir × %d feature (events=%s)",
        asset, n_train, n_features, event_ids,
    )

    models_dir.mkdir(parents=True, exist_ok=True)

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
    import joblib  # noqa: PLC0415

    joblib.dump(if_model, if_path)
    logger.info("IF model kaydedildi: %s", if_path)

    # 3) Autoencoder egitimi — status filter zaten uygulandi (status_types=None)
    ae = AutoencoderAnomalyEngine(hidden_layer_sizes=(32, 8, 32))
    ae.train(train_matrix, status_types=None)
    ae_path = models_dir / AE_FILENAME_TEMPLATE.format(asset=asset)
    ae.save(ae_path)
    logger.info(
        "AE model kaydedildi: %s (threshold=%.4f)", ae_path, ae.threshold,
    )

    return {
        "if_path": if_path,
        "ae_path": ae_path,
        "n_train": n_train,
        "n_features": n_features,
        "ae_threshold": ae.threshold,
    }


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

    print(f"Egitilecek asset sayisi: {len(asset_events)}")  # noqa: T201
    summary: list[dict[str, object]] = []
    for asset, ev_list in sorted(asset_events.items()):
        result = train_asset_models(asset, ev_list, datasets_dir, models_dir)
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
    failed = sum(1 for r in summary if r["if_path"] is None or r["ae_path"] is None)
    return 0 if failed == 0 else 1


def build_argparser() -> argparse.ArgumentParser:
    """CLI argumanlari."""
    parser = argparse.ArgumentParser(
        description=(
            "Fraunhofer CARE Wind Farm A CSV'lerinden dogrudan asset bazinda "
            "Isolation Forest + sklearn Autoencoder modeli egitir."
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
