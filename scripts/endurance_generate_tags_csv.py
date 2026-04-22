"""Endurance 200 tag bulk import CSV üretir.

Kaynak: `custos.simulator.sensors.build_endurance_sensors()`. Çıktı, dashboard
bulk import akışına (`POST /sensors/bulk-import`) doğrudan yüklenebilir formatta
CSV'dir. Tek gerçek kaynak ilkesi: katalog değişirse CSV de güncel üretilir.

Polling preset dağılımı (sırayla 200 tag üzerinde):
    T001-T150  → slow   (10000 ms) — sıcaklık + basınç + enerji
    T151-T195  → normal (1000 ms)  — RPM + ilk 15 durum biti
    T196-T200  → fast   (100 ms)   — son 5 durum biti

Kullanım:
    python scripts/endurance_generate_tags_csv.py
    python scripts/endurance_generate_tags_csv.py --out /tmp/endurance.csv
    python scripts/endurance_generate_tags_csv.py --host 192.168.1.50 --port 5020
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import Any

from custos.simulator.sensors import SensorDef, build_endurance_sensors

# Bulk import konvansiyonel holding register tabanı (40001 = protokol 0)
_MODBUS_HOLDING_BASE = 40001

# Polling preset dağılımı — cumulative upper bound (1-based T-index)
_POLLING_SLOW_MAX = 150  # T001..T150 → 10000 ms
_POLLING_NORMAL_MAX = 195  # T151..T195 → 1000 ms
# Kalan (T196..T200) → 100 ms (fast)

# Varsayılan dosya yolu (repo dışı, kullanıcı veri alanı)
DEFAULT_OUTPUT = Path("_personal/pilot/endurance_tags_200.csv")

# CSV başlıkları — bulk_import.py `BulkImportRow` alan isimleriyle birebir eşleşir.
_CSV_COLUMNS: tuple[str, ...] = (
    "tag_id",
    "name",
    "modbus_host",
    "modbus_port",
    "unit_id",
    "register_address",
    "register_type",
    "byte_order",
    "gain",
    "offset",
    "unit",
    "polling_interval_ms",
)


def _polling_interval_ms(tag_index: int) -> int:
    """1-based tag sırasına göre polling preset dağıtır.

    Dağılım sabit: 150 slow + 45 normal + 5 fast = 200 toplam.
    """
    if tag_index <= _POLLING_SLOW_MAX:
        return 10000
    if tag_index <= _POLLING_NORMAL_MAX:
        return 1000
    return 100


def _row_for_sensor(
    sensor: SensorDef,
    tag_index: int,
    modbus_host: str,
    modbus_port: int,
    unit_id: int,
) -> dict[str, Any]:
    """Bir SensorDef'i bulk import CSV satırına çevirir.

    Register adresi 40001 tabanlı konvansiyonel (bulk_import kabul eder ve
    protokol adresine indirger).
    """
    return {
        "tag_id": sensor.tag_id,
        "name": sensor.name,
        "modbus_host": modbus_host,
        "modbus_port": modbus_port,
        "unit_id": unit_id,
        "register_address": _MODBUS_HOLDING_BASE + sensor.register,
        "register_type": "uint16",
        "byte_order": "big",
        "gain": sensor.gain,
        "offset": sensor.offset,
        "unit": sensor.unit,
        "polling_interval_ms": _polling_interval_ms(tag_index),
    }


def generate_csv(
    output_path: Path,
    modbus_host: str = "127.0.0.1",
    modbus_port: int = 5020,
    unit_id: int = 1,
) -> int:
    """200 endurance tag'ini CSV'ye yazar. Yazılan satır sayısını döndürür."""
    sensors = build_endurance_sensors()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(_CSV_COLUMNS))
        writer.writeheader()
        for idx, sensor in enumerate(sensors, start=1):
            writer.writerow(
                _row_for_sensor(
                    sensor,
                    tag_index=idx,
                    modbus_host=modbus_host,
                    modbus_port=modbus_port,
                    unit_id=unit_id,
                ),
            )
    return len(sensors)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Endurance test için 200 tag'lik bulk import CSV'si üret",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Çıktı dosyası yolu (varsayılan: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Tag'lerin Modbus host adresi (varsayılan: 127.0.0.1 — endurance loopback)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=5020,
        help="Modbus TCP portu (varsayılan: 5020 — simülatör)",
    )
    parser.add_argument(
        "--unit-id",
        type=int,
        default=1,
        help="Modbus unit_id (varsayılan: 1)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point — CSV üretir ve ekrana özet basar."""
    args = _parse_args(argv)
    count = generate_csv(
        output_path=args.out,
        modbus_host=args.host,
        modbus_port=args.port,
        unit_id=args.unit_id,
    )
    print(  # noqa: T201 — CLI çıktısı
        f"Endurance CSV üretildi: {args.out} ({count} tag, "
        f"host={args.host}:{args.port}, unit_id={args.unit_id})",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
