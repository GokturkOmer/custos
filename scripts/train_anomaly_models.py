"""Anomali modeli eğitim script'i — offline çalışır.

Tüm aktif asset instance'lar (veya tek bir instance) için
Isolation Forest modeli eğitir ve data/models/ dizinine yazar.

Kullanım:
    python scripts/train_anomaly_models.py
    python scripts/train_anomaly_models.py --instance-id 1
    python scripts/train_anomaly_models.py --lookback-hours 48
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from custos.analytics.anomaly_detector import train_model_for_instance
from custos.shared.config import settings
from custos.shared.database import create_database

# Model dizini — proje kökünde data/models/
MODELS_DIR = Path("data/models")


async def main(instance_id: int | None, lookback_hours: int) -> int:
    """Ana eğitim fonksiyonu."""
    db = create_database(settings)
    await db.connect()

    try:
        if instance_id is not None:
            # Tek instance
            output_path = MODELS_DIR / f"anomaly_{instance_id}.joblib"
            success = await train_model_for_instance(
                db, instance_id, output_path, lookback_hours,
            )
            if success:
                print(f"Model eğitildi: {output_path}")  # noqa: T201
                return 0
            print(f"Model eğitilemedi (yetersiz veri?): instance_id={instance_id}")  # noqa: T201
            return 1
        else:
            # Tüm aktif instance'lar
            instances = await db.list_asset_instances(status="active")
            if not instances:
                print("Aktif instance bulunamadı.")  # noqa: T201
                return 1

            trained = 0
            for inst in instances:
                assert inst.id is not None
                output_path = MODELS_DIR / f"anomaly_{inst.id}.joblib"
                success = await train_model_for_instance(
                    db, inst.id, output_path, lookback_hours,
                )
                if success:
                    trained += 1
                    print(f"  [{inst.id}] {inst.name}: OK → {output_path}")  # noqa: T201
                else:
                    print(f"  [{inst.id}] {inst.name}: ATLA (yetersiz veri)")  # noqa: T201

            print(f"\nToplam: {trained}/{len(instances)} model eğitildi.")  # noqa: T201
            return 0 if trained > 0 else 1
    finally:
        await db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Anomali modeli eğitim script'i")
    parser.add_argument(
        "--instance-id",
        type=int,
        default=None,
        help="Tek bir instance için model eğit",
    )
    parser.add_argument(
        "--lookback-hours",
        type=int,
        default=24,
        help="Eğitim verisi için geriye bakış süresi (saat)",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.instance_id, args.lookback_hours)))
