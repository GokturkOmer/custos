"""Son okumaları sorgulayan yardımcı script.

Kullanım: python scripts/query_last_readings.py

Son 60 saniyedeki tüm sensörlerden gelen okumaların
özet tablosunu gösterir.
"""

import asyncio
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from custos.shared.config import settings
from custos.shared.database import TimescaleDBDatabase
from custos.shared.sensor_config import load_sensor_configs


async def main() -> int:
    """Son okumaları sorgular ve tablo formatında gösterir."""
    db = TimescaleDBDatabase(settings)

    try:
        await db.connect()
    except Exception as exc:
        print(f"FAIL: Veritabanına bağlanılamadı — {exc}")  # noqa: T201
        return 1

    try:
        sensors = load_sensor_configs(Path("config/sensors.toml"))
        now = datetime.now(UTC)
        start = now - timedelta(seconds=60)

        # Başlık
        header = f"{'Sensör':<10} {'Okuma':>6} {'Min':>10} {'Max':>10} {'Ortalama':>10} {'Son':>10}"
        print(header)  # noqa: T201
        print("-" * len(header))  # noqa: T201

        for sensor in sensors:
            readings = await db.query_raw_readings(sensor.id, start, now)
            ok_readings = [r for r in readings if r.quality_flag == 0]

            if not ok_readings:
                print(  # noqa: T201
                    f"{sensor.id:<10} {'0':>6} {'-':>10} {'-':>10} {'-':>10} {'-':>10}"
                )
                continue

            values = [r.value for r in ok_readings]
            min_val = min(values)
            max_val = max(values)
            avg_val = sum(values) / len(values)
            last_val = values[-1]

            print(  # noqa: T201
                f"{sensor.id:<10} {len(ok_readings):>6} "
                f"{min_val:>10.1f} {max_val:>10.1f} "
                f"{avg_val:>10.1f} {last_val:>10.1f}"
            )

        return 0

    except Exception as exc:
        print(f"FAIL: {exc}")  # noqa: T201
        return 1

    finally:
        await db.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
