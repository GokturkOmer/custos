"""Autoencoder anomaly model egitimi (Faz 1.3 wind pivot).

Offline egitim scripti. ``custos_wind`` DB'sinden tag reading'lerini ve
``wind_event_metadata.status_type_id``i ceker; sadece status ∈ {0, 2}
satirlar uzerinde sklearn MLPRegressor autoencoder egitir. Model dosyalari
``data/models/autoencoder_<instance_id>_wind.joblib`` formatinda yazilir.

Mevcut ``train_anomaly_models.py`` (IF) pattern'i ile parallel — ayni
asset_instance icin iki model dosyasi yan yana durur:

  data/models/anomaly_<id>.joblib            (IF, AVM + wind ortak)
  data/models/autoencoder_<id>_wind.joblib  (AE, sadece wind)

Production guard:
- ``POSTGRES_DB`` ``custos_wind`` olmali (AVM production'a yazmamak icin).
- Model dosya adlandirma ``_wind`` suffix'i ile IF dosyalariyla cakismaz.

Kullanim::

    set -a && source _personal/wind_pivot/.env.wind && set +a
    .venv/bin/python scripts/train_autoencoder_models.py \\
        --instance-id 1 --lookback-days 365 \\
        --hidden-layer-sizes 32,8,32
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import structlog

from custos.analytics.autoencoder_engine import (
    DEFAULT_THRESHOLD_QUANTILE,
    DEFAULT_VALID_STATUS_TYPES,
    AutoencoderAnomalyEngine,
)
from custos.shared.config import settings
from custos.shared.database import DatabaseInterface, create_database
from custos.shared.logging import configure_logging

logger = structlog.get_logger(logger_name="train_autoencoder")

EXPECTED_POSTGRES_DB = "custos_wind"
MODELS_DIR = Path("data/models")


def check_postgres_db_guard() -> str | None:
    """``POSTGRES_DB`` env var ``custos_wind`` mi? Yanlissa hata mesaji."""
    current = os.environ.get("POSTGRES_DB")
    if current != EXPECTED_POSTGRES_DB:
        return (
            f"HATA: POSTGRES_DB={current!r} (beklenen {EXPECTED_POSTGRES_DB!r}). "
            f".env.wind kaynaklayin."
        )
    return None


async def fetch_feature_matrix(
    db: DatabaseInterface,
    instance_id: int,
    lookback_days: int,
) -> tuple[np.ndarray, list[str]]:
    """Bir asset_instance icin son N gun tag reading'lerinden feature matrix kurar.

    Donus: (samples, tag_ids). Samples shape (n_samples, n_features). Her
    binding 1 feature; tum tag'lerin ayni miktarda reading'i olmasi
    garantili degil, en kisa seriye kirpilir.

    NOT — ``features`` tablosu boş kullanim:
        Custos'ta ``features`` tablosu (migration 003) seyrek populate
        edilir; F8/feature-engineering henüz aktif değil. Bu autoencoder
        ham ``tag_readings``i kullanir (raw sensor values). Z-score scaling
        + reconstruction error otomatik feature engineering yerine geçer
        (paper baseline'i de raw sensor değerleri ile çalisir). Faz 3'te
        rolling-window statistics gerekirse ``insert_feature`` aktive
        edilip burada birlestirilebilir; simdilik gerek yok.
    """
    bindings = await db.list_tag_bindings(instance_id)
    if not bindings:
        msg = f"Tag binding yok: instance_id={instance_id}"
        raise ValueError(msg)

    now = datetime.now(UTC)
    start = now - timedelta(days=lookback_days)
    tag_ids = [b.tag_id for b in bindings]

    series: list[list[float]] = []
    for tag_id in tag_ids:
        readings = await db.query_tag_readings(tag_id, start, now)
        series.append([r.value for r in readings])

    if not series or not all(series):
        msg = (
            f"Tag reading'leri eksik: instance_id={instance_id}, "
            f"bindings={len(bindings)}, "
            f"seri uzunluklari={[len(s) for s in series]}"
        )
        raise ValueError(msg)

    min_len = min(len(s) for s in series)
    matrix = np.column_stack([np.asarray(s[:min_len], dtype=np.float64) for s in series])
    return matrix, tag_ids


async def fetch_status_types(
    db: DatabaseInterface,
    instance_id: int,
    n_samples: int,
    lookback_days: int,
) -> np.ndarray | None:
    """``wind_event_metadata.status_type_id`` serisini ceker (varsa).

    AVM ortaminda wind_event_metadata yoktur (migration 039 sadece
    custos_wind'de tablo olusturuyor); o durumda None doner ve egitim
    tum satirlari kullanir (filtre yok). Wind ortaminda tablo var ise
    son ``n_samples`` satira esit boyutta dizi doner.

    Bu adim opsiyonel — DB interface'inde wind_event_metadata icin direct
    method yok; raw asyncpg query'siyle isleniyor. AVM'de hata olmamasi
    icin try/except.
    """
    # DB interface'de wind_event_metadata methodu yok; pool uzerinden raw
    # query. AVM ortaminda tablo yoksa exception yutulup None doner.
    pool = getattr(db, "_pool", None)
    if pool is None:
        return None
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT status_type_id FROM wind_event_metadata "
                "WHERE asset_instance_id = $1 "
                "ORDER BY timestamp DESC LIMIT $2",
                instance_id,
                n_samples,
            )
    except Exception:
        # Tablo yok veya sema farkli — silent fail, filter yok mod.
        return None
    if not rows:
        return None
    # Tag reading'leri timestamp ASC alindi (query_tag_readings default'u);
    # metadata DESC alindi — reverse et.
    status_arr = np.asarray([r["status_type_id"] for r in rows][::-1], dtype=np.int_)
    # Truncate to feature matrix length (en kisa seriye hizali).
    return status_arr[-n_samples:]


async def train_autoencoder_for_instance(
    db: DatabaseInterface,
    instance_id: int,
    output_path: Path,
    *,
    lookback_days: int,
    hidden_layer_sizes: tuple[int, ...],
    threshold_quantile: float,
    apply_status_filter: bool,
) -> bool:
    """Tek instance icin AE egit, joblib'a yaz.

    Donus: True = model yazildi, False = yetersiz veri / setup eksik.
    """
    instance = await db.get_asset_instance(instance_id)
    if instance is None:
        await logger.awarning("Instance bulunamadi", instance_id=instance_id)
        return False

    try:
        samples, tag_ids = await fetch_feature_matrix(
            db, instance_id, lookback_days,
        )
    except ValueError as exc:
        await logger.awarning(
            "Feature matrix kurulamadi",
            instance_id=instance_id,
            error=str(exc),
        )
        return False

    status_arr: np.ndarray | None = None
    if apply_status_filter:
        status_arr = await fetch_status_types(
            db, instance_id, samples.shape[0], lookback_days,
        )
        if status_arr is not None and status_arr.shape[0] != samples.shape[0]:
            await logger.awarning(
                "status_types boyutu uyumsuz; filtre yok mod",
                instance_id=instance_id,
                samples=samples.shape[0],
                status=status_arr.shape[0],
            )
            status_arr = None

    engine = AutoencoderAnomalyEngine(
        hidden_layer_sizes=hidden_layer_sizes,
        threshold_quantile=threshold_quantile,
    )
    try:
        engine.train(samples, status_types=status_arr)
    except ValueError as exc:
        await logger.awarning(
            "AE egitilemedi",
            instance_id=instance_id,
            error=str(exc),
            samples=samples.shape,
        )
        return False

    engine.save(output_path)
    await logger.ainfo(
        "AE model egitildi",
        instance_id=instance_id,
        path=str(output_path),
        features=samples.shape[1],
        train_rows=engine.n_train_samples,
        threshold=engine.threshold,
        tag_ids=tag_ids,
    )
    return True


def _parse_hidden_layer_sizes(spec: str) -> tuple[int, ...]:
    """``"32,8,32"`` → ``(32, 8, 32)``. Negatif/0 hatalidir."""
    parts = [p.strip() for p in spec.split(",") if p.strip()]
    sizes = tuple(int(p) for p in parts)
    if not sizes or any(s <= 0 for s in sizes):
        msg = f"hidden_layer_sizes gecersiz: {spec!r}"
        raise ValueError(msg)
    return sizes


async def run(args: argparse.Namespace) -> int:
    """Ana orchestrator: guard → DB → per-instance egitim → ozet."""
    guard_err = check_postgres_db_guard()
    if guard_err:
        print(guard_err, file=sys.stderr)  # noqa: T201
        return 2

    hidden_sizes = _parse_hidden_layer_sizes(args.hidden_layer_sizes)
    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    db = create_database(settings)
    await db.connect()
    try:
        if args.instance_id is not None:
            instance_ids: list[int] = [args.instance_id]
        else:
            instances = await db.list_asset_instances(status="active")
            instance_ids = [
                inst.id for inst in instances if inst.id is not None
            ]
            if not instance_ids:
                print(  # noqa: T201
                    "Aktif asset_instance yok — once seed_wind_tags.py kostur.",
                    file=sys.stderr,
                )
                return 1

        trained = 0
        for iid in instance_ids:
            output_path = MODELS_DIR / f"autoencoder_{iid}_wind.joblib"
            ok = await train_autoencoder_for_instance(
                db,
                iid,
                output_path,
                lookback_days=args.lookback_days,
                hidden_layer_sizes=hidden_sizes,
                threshold_quantile=args.threshold_quantile,
                apply_status_filter=not args.no_status_filter,
            )
            status = "OK" if ok else "SKIP"
            print(f"  [{iid}] {status} → {output_path}")  # noqa: T201
            if ok:
                trained += 1

        print(  # noqa: T201
            f"\nToplam: {trained}/{len(instance_ids)} AE modeli egitildi.",
        )
        return 0 if trained > 0 else 1
    finally:
        await db.close()


def build_argparser() -> argparse.ArgumentParser:
    """CLI argumanlari."""
    parser = argparse.ArgumentParser(
        description=(
            "Autoencoder anomaly modeli egitim scripti (wind pivot Faz 1.3). "
            "Mevcut IF modelleri ile yan yana calisir; engine_mode='both' "
            "ile her ikisi production'a yazar."
        ),
    )
    parser.add_argument(
        "--instance-id",
        type=int,
        default=None,
        help="Tek instance egit; verilmezse tum aktif instance'lar.",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=365,
        help="Egitim verisi geriye bakis (gun, default 365 — Hoinka prensibi).",
    )
    parser.add_argument(
        "--hidden-layer-sizes",
        default="32,8,32",
        help=(
            "Bottleneck dahil hidden katmanlar (virgul ile). "
            "Default '32,8,32' (paper baseline)."
        ),
    )
    parser.add_argument(
        "--threshold-quantile",
        type=float,
        default=DEFAULT_THRESHOLD_QUANTILE,
        help=(
            f"Reconstruction error threshold quantile "
            f"(default {DEFAULT_THRESHOLD_QUANTILE})."
        ),
    )
    parser.add_argument(
        "--no-status-filter",
        action="store_true",
        help=(
            f"wind_event_metadata.status_type_id filtresini DEVRE DISI birak. "
            f"Default: aktif (sadece status ∈ {list(DEFAULT_VALID_STATUS_TYPES)})."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry-point."""
    configure_logging("INFO")
    parser = build_argparser()
    args = parser.parse_args(argv)
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
