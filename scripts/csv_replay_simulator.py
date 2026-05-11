"""Fraunhofer CARE CSV → diagslave Modbus replay simulator (Faz 1.2).

Kapsam:
    Wind Farm A dataset'inde her 10dk'lik tick'i Custos production code
    path'inden gecirir:

      CSV (semicolon, 86 sensor + 5 metadata kolonu)
        |
        |--(a) Modbus path: pymodbus.AsyncModbusTcpClient
        |        write_registers(start, [...]) → diagslave 127.0.0.1:5021
        |        → Custos collector poll → tag_readings (custos_wind DB)
        |
        '--(b) Metadata path: asyncpg INSERT → wind_event_metadata
                (status_type_id, train_test, original_event_id,
                 original_asset_id, asset_instance_id, timestamp)

    Ikisi de ayni wall-clock NOW'da yazilir; JOIN (asset_instance_id,
    timestamp) ile birlestirilebilir (kucuk poll-delay toleransi
    icin BETWEEN window).

Mimari kural istisnasi (CLAUDE.md):
    "Modbus client kodunda write_register/write_registers ASLA" kurali
    production Custos collector icin gecerlidir. Bu dosya bir replay
    simulatoru (development tool); kontrolumuzdeki diagslave'e (port
    5021 — production 502'den izole) yazar. architecture_check.py
    scope'u (src/custos/**/*.py) scripts/ klasorunu kapsamadigi icin
    bu kullanim violation uretmez. drift_simulator.py ile ayni pattern.

Calistirma ornegi:
    python scripts/csv_replay_simulator.py \\
        --csv _personal/wind_pivot/raw/CARE_To_Compare/Wind\\ Farm\\ A/datasets/0.csv \\
        --tag-map _personal/wind_pivot/tag_map_farm_a.csv \\
        --asset-instance-id 1 \\
        --diagslave-host 127.0.0.1 --diagslave-port 5021 --slave-id 1 \\
        --speed 1000 \\
        --status-filter 0,2 \\
        --event-info _personal/wind_pivot/raw/CARE_To_Compare/Wind\\ Farm\\ A/event_info.csv \\
        --inject-ground-truth

PG baglantisi POSTGRES_{HOST,PORT,USER,PASSWORD,DB} env'lerinden okunur
(.env.wind kullan: ``set -a; source .env.wind; set +a``).
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg
from pymodbus.client import AsyncModbusTcpClient

# Bir Modbus PDU write_registers cagrisinda izin verilen max register sayisi.
# Spec'te 123 (0x7B); diagslave bunu zorlar.
MODBUS_MAX_WRITE_REGISTERS = 123

# Tum tick'in temel araligi (saniye). CARE dataset 10 dakika aggregate.
TICK_SECONDS_REAL = 10 * 60.0

# Default timeout (seconds) ModbusTcpClient bagsanti.
MODBUS_CONNECT_TIMEOUT = 3.0

# Per-tick Modbus write timeout (s). Speed=1000'de tick suresi ~0.6sn;
# write timeout bunun altinda olmali, yoksa drift birikir.
MODBUS_WRITE_TIMEOUT = 2.0

# Default status filtreleri (None = hicbir filtre).
_VALID_STATUS_IDS: tuple[int, ...] = (0, 1, 2, 3, 4, 5)

logger = logging.getLogger("csv_replay_simulator")


# --- Veri sinifi tanimlari ---


@dataclass(frozen=True)
class TagEntry:
    """tag_map_farm_a.csv satirinin runtime temsili.

    ``word_count`` register_type'tan turetilir (uint16/int16 → 1,
    uint32/int32 → 2). float32 destegi simdilik yok (Farm A'da yok).
    """

    sensor_name: str
    custos_tag_name: str
    register_address: int
    register_type: str
    gain: float
    offset: float

    @property
    def word_count(self) -> int:
        """Bu tag kac Modbus register kaplar (1 veya 2)."""
        return _REG_WORD_COUNT[self.register_type]


@dataclass(frozen=True)
class EventInfo:
    """event_info.csv satiri (asset bazli 1 olay)."""

    asset: str
    event_id: int
    event_label: str  # "anomaly" | "normal"
    event_start_id: int
    event_end_id: int
    event_description: str


# Desteklenen register tipleri → kapladigi 16-bit word sayisi.
_REG_WORD_COUNT: dict[str, int] = {
    "uint16": 1,
    "int16": 1,
    "uint32": 2,
    "int32": 2,
}


# --- Encoding fonksiyonlari ---


def _clamp(value: int, low: int, high: int) -> int:
    """Tam sayiyi [low, high] araligina sikistirir."""
    return max(low, min(high, value))


def _to_unsigned_16(signed: int) -> int:
    """int16 → uint16 (two's complement)."""
    signed = _clamp(signed, -0x8000, 0x7FFF)
    return signed & 0xFFFF


def _to_unsigned_32(signed: int) -> int:
    """int32 → uint32 (two's complement)."""
    signed = _clamp(signed, -0x80000000, 0x7FFFFFFF)
    return signed & 0xFFFFFFFF


def encode_value(physical: float, entry: TagEntry) -> list[int]:
    """Fiziksel deger → Modbus register word listesi (1 veya 2 word).

    Formul: raw = round((physical - offset) / gain).
    2-word tipler icin **word order "big"** (hi word once) — Custos
    register_decoder.py default'u.

    NaN/inf → uint16=0 yazilir, log uyarisi verilir; collector tarafinda
    ham 0 olarak gorunur (anomaly degil, sadece null reading).
    """
    if not _is_finite(physical):
        logger.warning(
            "NaN/inf deger %s (%s); 0 yazildi",
            entry.sensor_name,
            entry.custos_tag_name,
        )
        return [0] * entry.word_count

    raw = round((physical - entry.offset) / entry.gain)

    if entry.register_type == "uint16":
        return [_clamp(raw, 0, 0xFFFF)]
    if entry.register_type == "int16":
        return [_to_unsigned_16(raw)]
    if entry.register_type == "uint32":
        u = _clamp(raw, 0, 0xFFFFFFFF)
        return [(u >> 16) & 0xFFFF, u & 0xFFFF]
    if entry.register_type == "int32":
        u = _to_unsigned_32(raw)
        return [(u >> 16) & 0xFFFF, u & 0xFFFF]
    msg = f"Desteklenmeyen register_type: {entry.register_type!r}"
    raise ValueError(msg)


def _is_finite(value: float) -> bool:
    """math.isnan / isinf icin saf-kontrol; numpy import etmemek icin."""
    return value == value and value != float("inf") and value != float("-inf")


# --- Tag map / event info yukleyiciler ---


def load_tag_map(path: Path) -> list[TagEntry]:
    """tag_map_farm_a.csv → TagEntry listesi (sira korunur).

    Sira korunur ki ayni register'a bagli birden cok tag (yoksa)
    deterministic yazilsin.
    """
    entries: list[TagEntry] = []
    with path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rtype = row["register_type"].strip()
            if rtype not in _REG_WORD_COUNT:
                logger.warning(
                    "Atlanan tag: %s — desteklenmeyen register_type=%r",
                    row.get("sensor_name", "?"),
                    rtype,
                )
                continue
            entries.append(
                TagEntry(
                    sensor_name=row["sensor_name"].strip(),
                    custos_tag_name=row["custos_tag_name"].strip(),
                    register_address=int(row["register_address"]),
                    register_type=rtype,
                    gain=float(row["gain"]),
                    offset=float(row["offset"]),
                ),
            )
    return entries


def load_event_info(path: Path) -> dict[int, EventInfo]:
    """event_info.csv → {event_id: EventInfo} (semicolon-separated)."""
    events: dict[int, EventInfo] = {}
    with path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            ev = EventInfo(
                asset=row["asset"].strip(),
                event_id=int(row["event_id"]),
                event_label=row["event_label"].strip(),
                event_start_id=int(row["event_start_id"]),
                event_end_id=int(row["event_end_id"]),
                event_description=(row.get("event_description") or "").strip(),
            )
            events[ev.event_id] = ev
    return events


# --- Tick paketleyici ---


def build_register_block(
    row: dict[str, str],
    tag_entries: list[TagEntry],
) -> tuple[int, list[int]]:
    """Tek CSV satirini contiguous bir register bloguna paketler.

    Donus: (base_addr, words) — pymodbus write_registers(base_addr, words)
    ile tek atomik yazma yapilabilir.

    NaN/eksik degerler 0 ile doldurulur (encode_value uyari verir).
    Bos kalan offsetler (tag_map'te olmayan adresler arasi bosluk) da
    0 olarak yazilir — diagslave RAM'inde mevcut deger uzerine yazilir.
    """
    base_addr = min(t.register_address for t in tag_entries)
    max_addr = max(t.register_address + t.word_count - 1 for t in tag_entries)
    total = max_addr - base_addr + 1
    block: list[int] = [0] * total

    for entry in tag_entries:
        raw_value = row.get(entry.sensor_name, "")
        physical = _parse_float(raw_value, entry.sensor_name)
        words = encode_value(physical, entry)
        offset = entry.register_address - base_addr
        block[offset:offset + len(words)] = words

    return base_addr, block


def _parse_float(value: str, sensor_name: str) -> float:
    """Bos/'nan' degerler nan dondurur; encode_value 0 olarak yazar."""
    raw = (value or "").strip()
    if not raw or raw.lower() in {"nan", "null", "none"}:
        return float("nan")
    try:
        return float(raw)
    except ValueError:
        logger.warning("Parse hatasi: %s=%r → nan", sensor_name, raw)
        return float("nan")


# --- Status filtre ---


def parse_status_filter(spec: str | None) -> frozenset[int] | None:
    """``"0,2"`` veya ``"0-2"`` veya ``None`` → {0, 2} / range / None.

    None / bos string → hicbir filtre (tum status'lar gecer).
    """
    if not spec:
        return None
    items: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo_s, hi_s = part.split("-", 1)
            lo, hi = int(lo_s), int(hi_s)
            for v in range(lo, hi + 1):
                items.add(v)
        else:
            items.add(int(part))
    invalid = items - set(_VALID_STATUS_IDS)
    if invalid:
        msg = (
            f"Gecersiz status_type_id: {sorted(invalid)}. "
            f"Gecerli aralik: {list(_VALID_STATUS_IDS)}"
        )
        raise ValueError(msg)
    return frozenset(items)


# --- Async / Modbus / asyncpg yardimcilari ---


def _build_pg_dsn() -> str:
    """POSTGRES_* env vars → asyncpg DSN."""
    return (
        "postgresql://"
        f"{os.environ.get('POSTGRES_USER', 'custos')}"
        f":{os.environ.get('POSTGRES_PASSWORD', '')}"
        f"@{os.environ.get('POSTGRES_HOST', 'localhost')}"
        f":{os.environ.get('POSTGRES_PORT', '5432')}"
        f"/{os.environ.get('POSTGRES_DB', 'custos_wind')}"
    )


_INSERT_METADATA_SQL = """
    INSERT INTO wind_event_metadata (
        asset_instance_id, timestamp, status_type_id, train_test,
        original_event_id, original_asset_id
    ) VALUES ($1, $2, $3, $4, $5, $6)
    ON CONFLICT (asset_instance_id, timestamp) DO NOTHING
"""


# --- Ground truth marker (event window) ---


def _log_ground_truth(
    tick_id: int,
    events: dict[int, EventInfo],
    seen_starts: set[int],
    seen_ends: set[int],
) -> None:
    """CSV `id`'i event_info'daki start/end ile esleserse markerlog basar."""
    for ev in events.values():
        if ev.event_start_id == tick_id and ev.event_id not in seen_starts:
            logger.info(
                "[REPLAY] event %d (%s: %s) STARTED at csv_id=%d",
                ev.event_id,
                ev.event_label,
                ev.event_description or "(no desc)",
                tick_id,
            )
            seen_starts.add(ev.event_id)
        if ev.event_end_id == tick_id and ev.event_id not in seen_ends:
            logger.info(
                "[REPLAY] event %d (%s) ENDED at csv_id=%d",
                ev.event_id,
                ev.event_label,
                tick_id,
            )
            seen_ends.add(ev.event_id)


# --- Ana replay coroutine ---


_running = True


def _handle_sigterm(signum: int, _frame: object) -> None:
    """SIGTERM/SIGINT → graceful shutdown."""
    global _running
    logger.info("Sinyal %s alindi, simulator duruyor (graceful).", signum)
    _running = False


async def replay_csv(args: argparse.Namespace) -> int:
    """Ana replay isi. Modbus + asyncpg baglantisi + tick dongusu."""
    tag_entries = load_tag_map(Path(args.tag_map))
    logger.info("Tag map yuklendi: %d entry", len(tag_entries))

    events: dict[int, EventInfo] = {}
    if args.event_info:
        events = load_event_info(Path(args.event_info))
        logger.info("event_info yuklendi: %d event", len(events))

    status_filter = parse_status_filter(args.status_filter)
    if status_filter is not None:
        logger.info("Status filtresi aktif: %s", sorted(status_filter))

    # Modbus + PG block check
    block_check_addr, block_check = build_register_block(
        {e.sensor_name: "0" for e in tag_entries},
        tag_entries,
    )
    if len(block_check) > MODBUS_MAX_WRITE_REGISTERS:
        msg = (
            f"Tag map cok genis: {len(block_check)} word; "
            f"Modbus spec max {MODBUS_MAX_WRITE_REGISTERS}. "
            "Bloklara bol (Faz 2)."
        )
        raise ValueError(msg)
    logger.info(
        "Register blogu: addr %d, %d word (Modbus max %d)",
        block_check_addr,
        len(block_check),
        MODBUS_MAX_WRITE_REGISTERS,
    )

    # Modbus client
    client = AsyncModbusTcpClient(
        args.diagslave_host,
        port=args.diagslave_port,
        timeout=MODBUS_CONNECT_TIMEOUT,
    )
    connected = await client.connect()
    if not connected:
        logger.error(
            "diagslave'e baglanilamadi: %s:%s",
            args.diagslave_host,
            args.diagslave_port,
        )
        return 2

    # asyncpg connection
    pg_conn = await asyncpg.connect(dsn=_build_pg_dsn())
    logger.info(
        "Baglantilar OK: diagslave=%s:%s slave_id=%d, PG=%s",
        args.diagslave_host,
        args.diagslave_port,
        args.slave_id,
        os.environ.get("POSTGRES_DB", "custos_wind"),
    )

    sleep_per_tick = TICK_SECONDS_REAL / max(args.speed, 1.0)
    logger.info(
        "Speed=%.1fx → tick suresi %.3f saniye (10dk gercek → bu kadar)",
        args.speed,
        sleep_per_tick,
    )

    seen_starts: set[int] = set()
    seen_ends: set[int] = set()
    found_prediction = not args.start_from_prediction
    tick_processed = 0
    tick_skipped = 0
    csv_start = time.monotonic()

    try:
        with Path(args.csv).open(encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter=";")
            for row in reader:
                if not _running:
                    break

                # start-from-prediction: ilk "prediction" satirina kadar atla.
                tt = row.get("train_test", "").strip()
                if not found_prediction:
                    if tt == "prediction":
                        found_prediction = True
                        logger.info(
                            "İlk prediction satiri (id=%s), replay basliyor",
                            row.get("id", "?"),
                        )
                    else:
                        continue

                # status_type_id filtresi.
                try:
                    status_id = int(row["status_type_id"])
                except (KeyError, ValueError):
                    logger.warning(
                        "status_type_id parse edilemedi, satir atlandi: %r",
                        row.get("status_type_id"),
                    )
                    continue

                if status_filter is not None and status_id not in status_filter:
                    tick_skipped += 1
                    continue

                # Register paketle + diagslave'e yaz.
                base_addr, block = build_register_block(row, tag_entries)
                try:
                    write_result = await asyncio.wait_for(
                        client.write_registers(  # allow-arch-check: simulator helper
                            base_addr,
                            block,
                            device_id=args.slave_id,
                        ),
                        timeout=MODBUS_WRITE_TIMEOUT,
                    )
                except TimeoutError:
                    logger.error(
                        "Modbus write timeout (%.1fs); tick atlandi (csv_id=%s)",
                        MODBUS_WRITE_TIMEOUT,
                        row.get("id"),
                    )
                    tick_skipped += 1
                    continue

                if write_result.isError():
                    logger.error(
                        "Modbus write hatasi: %s (csv_id=%s)",
                        write_result,
                        row.get("id"),
                    )
                    tick_skipped += 1
                    continue

                # wind_event_metadata INSERT (asyncpg).
                now = datetime.now(UTC)
                try:
                    csv_id = int(row.get("id", "0"))
                except ValueError:
                    csv_id = 0
                await pg_conn.execute(
                    _INSERT_METADATA_SQL,
                    args.asset_instance_id,
                    now,
                    status_id,
                    tt or "unknown",
                    csv_id,
                    row.get("asset_id", "").strip() or None,
                )

                # Ground truth markers.
                if args.inject_ground_truth and events:
                    _log_ground_truth(csv_id, events, seen_starts, seen_ends)

                tick_processed += 1

                if tick_processed % 100 == 0:
                    elapsed = time.monotonic() - csv_start
                    logger.info(
                        "Tick #%d (csv_id=%d, status=%d, %s) — %.1fs elapsed",
                        tick_processed,
                        csv_id,
                        status_id,
                        tt,
                        elapsed,
                    )

                if sleep_per_tick > 0:
                    await asyncio.sleep(sleep_per_tick)

    finally:
        await pg_conn.close()
        client.close()
        elapsed = time.monotonic() - csv_start
        logger.info(
            "Simulator durdu. tick_processed=%d tick_skipped=%d elapsed=%.1fs",
            tick_processed,
            tick_skipped,
            elapsed,
        )

    return 0


# --- CLI ---


def _build_argparser() -> argparse.ArgumentParser:
    """Komut satiri argumanlari (Faz 1.2 spec'i)."""
    parser = argparse.ArgumentParser(
        description=(
            "Fraunhofer CARE wind turbine CSV → diagslave Modbus replay. "
            "Sadece scripts/ icinde (test ortami). Production code path: "
            "diagslave → collector → custos_wind DB."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--csv",
        required=True,
        help="Replay edilecek CSV dosyasi (Fraunhofer CARE Farm A format).",
    )
    parser.add_argument(
        "--tag-map",
        required=True,
        help="tag_map_farm_a.csv yolu (sensor → register mapping).",
    )
    parser.add_argument(
        "--asset-instance-id",
        type=int,
        required=True,
        help=(
            "Custos asset_instances.id (bu CSV hangi turbin instance'a "
            "ait). wind_event_metadata INSERT'lerinde kullanilir."
        ),
    )
    parser.add_argument(
        "--diagslave-host",
        default="127.0.0.1",
        help="diagslave Modbus TCP host (default: 127.0.0.1).",
    )
    parser.add_argument(
        "--diagslave-port",
        type=int,
        default=5021,
        help=(
            "diagslave Modbus TCP portu (default: 5021 — wind izolasyonu; "
            "AVM production 502 ile cakismaz)."
        ),
    )
    parser.add_argument(
        "--slave-id",
        type=int,
        default=1,
        help="Modbus slave/device id (default: 1).",
    )
    parser.add_argument(
        "--speed",
        type=float,
        default=1.0,
        help=(
            "Hizlandirma faktoru. 1.0 = gercek zaman (10 dk/tick); "
            "1000.0 = 0.6 sn/tick (default 1.0)."
        ),
    )
    parser.add_argument(
        "--status-filter",
        default=None,
        help=(
            "Sadece bu status_type_id'leri replay et. Format: '0,2' veya "
            "'0-2'. Bos = filtre yok (default)."
        ),
    )
    parser.add_argument(
        "--start-from-prediction",
        action="store_true",
        help=(
            "Replay'i ilk train_test=='prediction' satirindan baslat. "
            "Test set degerlendirmesi icin."
        ),
    )
    parser.add_argument(
        "--event-info",
        default=None,
        help=(
            "event_info.csv yolu (opsiyonel). --inject-ground-truth ile "
            "birlikte kullanilir."
        ),
    )
    parser.add_argument(
        "--inject-ground-truth",
        action="store_true",
        help=(
            "event_info'daki event_start_id/event_end_id'lerine ulasildiginda "
            "log marker'i bas (ML degerlendirme icin)."
        ),
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging seviyesi (default: INFO).",
    )
    return parser


def _setup_logging(level: str) -> None:
    """stdout'a yapilandirilmis log (drift_simulator ile ayni format)."""
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        stream=sys.stdout,
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry-point: argparse → setup → asyncio.run(replay_csv)."""
    parser = _build_argparser()
    args = parser.parse_args(argv)
    _setup_logging(args.log_level)

    signal.signal(signal.SIGTERM, _handle_sigterm)
    signal.signal(signal.SIGINT, _handle_sigterm)

    try:
        return asyncio.run(replay_csv(args))
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt — exit.")
        return 130


# Public re-exports — testlerin import etmesi icin sade arayuz.
__all__ = [
    "EventInfo",
    "TagEntry",
    "build_register_block",
    "encode_value",
    "load_event_info",
    "load_tag_map",
    "main",
    "parse_status_filter",
]


def _module_self_check() -> Any:  # pragma: no cover
    """Smoke check — argparse yardimi rendered olur mu (subprocess test icin)."""
    return _build_argparser().format_help()


if __name__ == "__main__":
    sys.exit(main())
