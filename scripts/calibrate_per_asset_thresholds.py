"""Per-asset adaptive threshold kalibrasyon scripti (Wind pivot Faz 2 Prompt 2).

Wind pivot Faz 2 Prompt 2 (2026-05-12). Faz 2 Prompt 2 imp adimi:
``PerAssetThresholdCalibrator`` API hazirdi ama ``asset_thresholds``
tablosu bos kaldigi icin ``AnomalyDetector`` her zaman fallback (model
global threshold) kullaniyordu. Bu script:

1. Wind Farm A CSV'lerinden her asset icin training partition (paper
   kurali: ``train_test='train'`` + ``status_type_id ∈ {0, 2}``)
   satirlarini topla.
2. Eğitilmis IF + AE modelini yukle.
3. IF score_samples → quantile(0.01) alt kuyruk, AE RMSE →
   quantile(0.99) ust kuyruk olarak threshold hesapla.
4. ``PerAssetThresholdCalibrator.calibrate`` ile ``asset_thresholds``
   tablosuna UPSERT et (ON CONFLICT update). 5 asset × 2 engine = 10
   kayit beklenir.

Tipik kosturma::

    set -a; source _personal/wind_pivot/.env.wind; set +a
    PYTHONPATH=src .venv/bin/python scripts/calibrate_per_asset_thresholds.py \\
        --datasets-dir "_personal/wind_pivot/raw/CARE_To_Compare/Wind Farm A/datasets" \\
        --event-info  "_personal/wind_pivot/raw/CARE_To_Compare/Wind Farm A/event_info.csv" \\
        --tag-map     "_personal/wind_pivot/tag_map_farm_a.csv" \\
        --asset-template data/asset_templates/wind_turbine_v1.yaml \\
        --models-dir  data/models \\
        --assets 0,10,11,13,21

``--dry-run`` ile DB yazimi yapilmadan threshold'lar hesaplanip stdout'a
basilir (kalibrasyon sweep'i icin). DB yazimi varsayilan davranistir
(``CUSTOS_PER_ASSET_THRESHOLD`` env'i bu script icinde explicit ``True``
olarak ayarlanir — caller env'i 'off' birakmis olabilir).

Asset listesi cozumu (sira):
1. ``--assets`` CLI arg → Fraunhofer asset ID'leri (virgül ayraçli int).
2. Yoksa ``--event-info`` taranir → goründüğü tum asset id'leri kullanilir
   (Wind Farm A icin 0/10/11/13/21).
3. ``--instances-from-db`` flagi → DB'deki wind asset_instance'lari
   sorgulanir (template slug ``wind_turbine_v1``). Custos DB'sinde wind
   turbin instance'lari seed edildiyse bunu tercih edin; Faz 2 Prompt 2
   benchmark icin Fraunhofer ID semantigi daha basit.
"""

from __future__ import annotations

import argparse
import asyncio
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
from custos.analytics.per_asset_threshold import (
    DEFAULT_QUANTILE_AE,
    DEFAULT_QUANTILE_IF,
    PerAssetThresholdCalibrator,
)
from custos.shared.config import Settings
from custos.shared.database import TimescaleDBDatabase

if TYPE_CHECKING:
    from numpy.typing import NDArray

logger = logging.getLogger("calibrate_per_asset")

# Fraunhofer CARE Wind Farm A meta kolonlari — feature olarak sayilmazlar.
# train_wind_models_from_csv.py ile birebir uyumlu.
META_COLS: frozenset[str] = frozenset({
    "time_stamp",
    "asset_id",
    "id",
    "train_test",
    "status_type_id",
})

# Wind Farm A default asset listesi — event_info.csv tarandiginda goruldu.
DEFAULT_FARM_A_ASSETS: tuple[int, ...] = (0, 10, 11, 13, 21)


def _safe_float(value: str | None) -> float:
    """CSV float parse; bos / 'nan' / hata → 0.0 (AE NaN kabul etmez)."""
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

    train_wind_models_from_csv.py ile bire bir uyumlu. Skip: int'e
    cevrilemeyen asset / event id satirlari.
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


def collect_training_features(
    asset: int,
    event_ids: list[int],
    datasets_dir: Path,
) -> NDArray[np.float64]:
    """Asset'in tum event CSV'lerinden training partition + status filter
    satirlarini toplar.

    Paper kurali (train_wind_models_from_csv.py ile ayni):
    - ``train_test == 'train'`` (test seti karistirilmaz).
    - ``status_type_id ∈ {0, 2}`` (Normal + Idling).

    Donus: shape ``(n_train, n_features)``. Hicbir geçerli satir yoksa
    ``(0, 0)`` shape.
    """
    valid_status = set(DEFAULT_VALID_STATUS_TYPES)
    rows: list[list[float]] = []
    sensor_cols_ref: list[str] | None = None

    for ev_id in event_ids:
        csv_path = datasets_dir / f"{ev_id}.csv"
        if not csv_path.exists():
            logger.warning(
                "Asset %d: event %d CSV bulunamadi: %s", asset, ev_id, csv_path,
            )
            continue
        with csv_path.open(encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter=";")
            cols = [
                c for c in (reader.fieldnames or [])
                if c not in META_COLS
            ]
            if sensor_cols_ref is None:
                sensor_cols_ref = cols
            elif cols != sensor_cols_ref:
                logger.warning(
                    "Asset %d: event %d kolon yapisi farkli — atlandi",
                    asset, ev_id,
                )
                continue
            for row in reader:
                if (row.get("train_test") or "").strip() != "train":
                    continue
                if _safe_int(row.get("status_type_id")) not in valid_status:
                    continue
                rows.append(
                    [_safe_float(row.get(c, "")) for c in sensor_cols_ref],
                )
    if not rows or sensor_cols_ref is None:
        return np.zeros((0, 0), dtype=np.float64)
    return np.asarray(rows, dtype=np.float64)


def compute_if_scores(
    model_path: Path,
    features: NDArray[np.float64],
) -> NDArray[np.float64] | None:
    """Egitilmis IF modelini yukle ve ``score_samples`` doner.

    Eksik dosya → None (caller'a uyari + skip). Feature sayisi uyumsuz →
    None + uyari (sessizce yanlis threshold cikmasin).
    """
    if not model_path.exists():
        logger.warning("IF modeli bulunamadi: %s", model_path)
        return None
    import joblib  # noqa: PLC0415

    model = joblib.load(model_path)
    expected = getattr(model, "n_features_in_", features.shape[1])
    if features.shape[1] != expected:
        logger.warning(
            "IF feature sayisi uyumsuz: training=%d, model=%d",
            features.shape[1], expected,
        )
        return None
    scores: NDArray[np.float64] = model.score_samples(features)
    return scores.astype(np.float64)


def compute_ae_scores(
    model_path: Path,
    features: NDArray[np.float64],
) -> NDArray[np.float64] | None:
    """Egitilmis AE modelini yukle ve RMSE per row doner.

    Eksik dosya → None. Feature sayisi uyumsuz → None + uyari.
    """
    if not model_path.exists():
        logger.warning("AE modeli bulunamadi: %s", model_path)
        return None
    engine = AutoencoderAnomalyEngine.load(model_path)
    if features.shape[1] != engine.n_features:
        logger.warning(
            "AE feature sayisi uyumsuz: training=%d, model=%d",
            features.shape[1], engine.n_features,
        )
        return None
    return engine.score(features)


def resolve_assets(
    args: argparse.Namespace,
    event_info_path: Path,
) -> list[int]:
    """CLI argumanlarindan kalibrasyon yapilacak asset listesini cozer.

    Sira (ilk uygulanan kazanir):
    1. ``--assets 0,10,11,13,21`` → explicit liste.
    2. ``--event-info`` taranir → tum benzersiz asset_id'ler.
    3. DEFAULT_FARM_A_ASSETS fallback (defansif; event_info eksikse).

    DB-based lookup (``--instances-from-db``) ayri bir kod yolu — bu
    fonksiyon CSV-based asset listesini doner, DB lookup ana fonksiyonda.
    """
    if args.assets:
        return [int(a.strip()) for a in args.assets.split(",") if a.strip()]
    if event_info_path.exists():
        asset_events = load_event_info_assets(event_info_path)
        if asset_events:
            return sorted(asset_events.keys())
    return list(DEFAULT_FARM_A_ASSETS)


async def fetch_db_instance_ids(
    db: TimescaleDBDatabase,
    template_slug: str,
) -> list[int]:
    """DB'den ``template.slug == template_slug`` instance ID'lerini doner.

    Bos liste → caller fallback Fraunhofer ID'lerine geceer. Burada
    sessizce bos doniyoruz cunku Faz 2 Prompt 2 benchmark'ta DB seed'i
    olmadan calismayi destekliyoruz.
    """
    templates = await db.list_asset_templates()
    target_template = next(
        (t for t in templates if t.slug == template_slug),
        None,
    )
    if target_template is None or target_template.id is None:
        return []
    instances = await db.list_asset_instances()
    return [
        inst.id for inst in instances
        if inst.template_id == target_template.id and inst.id is not None
    ]


async def run(args: argparse.Namespace) -> int:
    """Ana akis — DB'ye baglan, asset'leri belirle, IF + AE kalibre et."""
    datasets_dir = Path(args.datasets_dir)
    event_info_path = Path(args.event_info)
    models_dir = Path(args.models_dir)
    template_slug = args.template_slug

    if not datasets_dir.exists():
        logger.error("datasets-dir bulunamadi: %s", datasets_dir)
        return 2
    if not event_info_path.exists():
        logger.error("event-info bulunamadi: %s", event_info_path)
        return 2

    # Asset listesi cozumu
    assets = resolve_assets(args, event_info_path)
    if not assets:
        logger.error("Kalibrasyon yapilacak asset yok")
        return 1
    print(f"Kalibrasyon yapilacak asset sayisi: {len(assets)} → {assets}")  # noqa: T201

    # Event ID map'i (asset_id → [event_id, ...])
    asset_events = load_event_info_assets(event_info_path)

    # DB baglantisi (dry-run'da bile baglaniyoruz ki DB-instance lookup
    # ihtiyaci karsilansin; calibrator enabled=True ama dry-run iken yazim
    # bypass edilir — asagida calibrate yerine direkt np.quantile uygularsak).
    settings = Settings()
    db = TimescaleDBDatabase(settings)
    await db.connect()
    try:
        # DB-from-instances kullanici tercih ediyorsa override et
        if args.instances_from_db:
            db_instance_ids = await fetch_db_instance_ids(db, template_slug)
            if db_instance_ids:
                logger.info(
                    "DB'den %d wind instance (template=%r) bulundu, asset listesi override",
                    len(db_instance_ids), template_slug,
                )
                # DB instance ID'leri Fraunhofer ID'lerinden farkli olabilir;
                # zip ile esle (sira korunarak) — Custos asset_instance.id
                # asset_thresholds.asset_instance_id'ye yazilacak.
                if len(db_instance_ids) != len(assets):
                    logger.warning(
                        "DB instance sayisi (%d) Fraunhofer asset sayisi "
                        "(%d) ile farkli — sira-bazli eslesme kullanilacak",
                        len(db_instance_ids), len(assets),
                    )
                pairs = list(zip(db_instance_ids, assets, strict=False))
            else:
                logger.info(
                    "DB'de wind instance yok — Fraunhofer ID'leri "
                    "asset_instance_id olarak kullanilacak",
                )
                pairs = [(asset, asset) for asset in assets]
        else:
            pairs = [(asset, asset) for asset in assets]

        # Calibrator — DB yazimi icin explicit enabled=True (env'den
        # bagimsiz). Dry-run iken calibrate yerine sadece np.quantile.
        calibrator = PerAssetThresholdCalibrator(db=db, enabled=True)

        # Kalibrasyon tablosu
        summary: list[dict[str, object]] = []
        for db_asset_id, fraunhofer_id in pairs:
            event_ids = asset_events.get(fraunhofer_id, [])
            if not event_ids:
                logger.warning(
                    "Asset %d (Fraunhofer): event_info'da event yok — atlandi",
                    fraunhofer_id,
                )
                summary.append({
                    "db_id": db_asset_id,
                    "fraunhofer_id": fraunhofer_id,
                    "if": None, "ae": None, "n_train": 0,
                })
                continue

            features = collect_training_features(
                fraunhofer_id, event_ids, datasets_dir,
            )
            if features.size == 0:
                logger.warning(
                    "Asset %d: training verisi bulunamadi", fraunhofer_id,
                )
                summary.append({
                    "db_id": db_asset_id,
                    "fraunhofer_id": fraunhofer_id,
                    "if": None, "ae": None, "n_train": 0,
                })
                continue

            n_train, n_features = features.shape
            logger.info(
                "Asset %d (db_id=%d): %d satir × %d feature",
                fraunhofer_id, db_asset_id, n_train, n_features,
            )

            row: dict[str, object] = {
                "db_id": db_asset_id,
                "fraunhofer_id": fraunhofer_id,
                "n_train": n_train,
                "n_features": n_features,
                "if": None,
                "ae": None,
            }

            # IF kalibrasyonu (alt kuyruk)
            if_scores = compute_if_scores(
                models_dir / f"anomaly_{fraunhofer_id}.joblib",
                features,
            )
            if if_scores is not None:
                if args.dry_run:
                    threshold = float(np.quantile(if_scores, args.quantile_if))
                else:
                    record = await calibrator.calibrate(
                        asset_instance_id=db_asset_id,
                        engine_type="if",
                        training_scores=if_scores,
                        quantile=args.quantile_if,
                    )
                    threshold = record.threshold
                row["if"] = threshold

            # AE kalibrasyonu (ust kuyruk)
            ae_scores = compute_ae_scores(
                models_dir / f"autoencoder_{fraunhofer_id}_wind.joblib",
                features,
            )
            if ae_scores is not None:
                if args.dry_run:
                    threshold = float(np.quantile(ae_scores, args.quantile_ae))
                else:
                    record = await calibrator.calibrate(
                        asset_instance_id=db_asset_id,
                        engine_type="ae",
                        training_scores=ae_scores,
                        quantile=args.quantile_ae,
                    )
                    threshold = record.threshold
                row["ae"] = threshold

            summary.append(row)

        # stdout tablosu
        print()  # noqa: T201
        print(  # noqa: T201
            "=== Per-Asset Threshold Kalibrasyon Ozeti ===",
        )
        header = (
            f"{'db_id':>6} | {'frnh_id':>7} | {'n_train':>7} | "
            f"{'n_feat':>6} | {'IF threshold':>14} | {'AE threshold':>14}"
        )
        print(header)  # noqa: T201
        print("-" * len(header))  # noqa: T201
        for r in summary:
            if_str = f"{r['if']:.6f}" if r["if"] is not None else "— (yok)"
            ae_str = f"{r['ae']:.6f}" if r["ae"] is not None else "— (yok)"
            print(  # noqa: T201
                f"{r['db_id']:>6} | {r['fraunhofer_id']:>7} | "
                f"{r['n_train']:>7} | {r['n_features']:>6} | "
                f"{if_str:>14} | {ae_str:>14}",
            )

        if args.dry_run:
            print("\n[dry-run] DB yazimi yapilmadi.")  # noqa: T201
        else:
            successful = sum(
                1 for r in summary
                if r["if"] is not None or r["ae"] is not None
            )
            n_written = sum(
                int(r["if"] is not None) + int(r["ae"] is not None)
                for r in summary
            )
            print(  # noqa: T201
                f"\nDB'ye yazilan kayit: {n_written} "
                f"({successful}/{len(pairs)} asset)",
            )
    finally:
        await db.close()

    return 0


def build_argparser() -> argparse.ArgumentParser:
    """CLI argumanlari."""
    parser = argparse.ArgumentParser(
        description=(
            "Wind Farm A CSV'lerinden asset basina IF + AE adaptive "
            "threshold'larini hesaplar ve asset_thresholds tablosuna "
            "UPSERT eder. PerAssetThresholdCalibrator API'sini kullanir."
        ),
    )
    parser.add_argument(
        "--datasets-dir",
        required=True,
        help="<event_id>.csv dosyalarinin oldugu dizin",
    )
    parser.add_argument(
        "--event-info",
        required=True,
        help="event_info.csv yolu — asset_id → event_id eslemesi icin",
    )
    parser.add_argument(
        "--tag-map",
        default=None,
        help=(
            "tag_map_farm_a.csv (opsiyonel — kalibrasyon icin gerekli "
            "degildir, semantik tutarlilik icin kabul edilir)"
        ),
    )
    parser.add_argument(
        "--asset-template",
        default="data/asset_templates/wind_turbine_v1.yaml",
        help=(
            "Asset template YAML (opsiyonel — kalibrasyon icin gerekli "
            "degildir, --instances-from-db ile birlikte slug match icin)"
        ),
    )
    parser.add_argument(
        "--template-slug",
        default="wind_turbine_v1",
        help="DB asset_template slug'i (default: wind_turbine_v1)",
    )
    parser.add_argument(
        "--models-dir",
        default="data/models",
        help=(
            "Egitilmis IF + AE joblib dosyalarinin dizini "
            "(default data/models)"
        ),
    )
    parser.add_argument(
        "--assets",
        default=None,
        help=(
            "Kalibre edilecek Fraunhofer asset ID listesi (virgül "
            "ayraçli). Verilmezse event_info.csv'deki tum asset'ler."
        ),
    )
    parser.add_argument(
        "--instances-from-db",
        action="store_true",
        help=(
            "DB asset_instances tablosundan wind turbin'leri okuyup "
            "Custos asset_instance.id'leri asset_thresholds'a yaz. "
            "Default: Fraunhofer ID = asset_instance_id (DB seed yoksa)."
        ),
    )
    parser.add_argument(
        "--quantile-if",
        type=float,
        default=DEFAULT_QUANTILE_IF,
        help=(
            f"IF threshold quantile (alt kuyruk). Default "
            f"{DEFAULT_QUANTILE_IF}."
        ),
    )
    parser.add_argument(
        "--quantile-ae",
        type=float,
        default=DEFAULT_QUANTILE_AE,
        help=(
            f"AE threshold quantile (ust kuyruk). Default "
            f"{DEFAULT_QUANTILE_AE}."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "DB yazimi yapma, sadece threshold'lari hesaplayip stdout'a "
            "yaz. Kalibrasyon sweep'i icin."
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
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
