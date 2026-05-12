"""Anomaly event'leri uzerinde lead-time analizi (wind pivot Faz 1.5).

Bir anomaly event icin, model'in ilk anomaly tespiti ile resmi
``event_start_id`` arasindaki sure farkini hesaplar (lead-time). Pozitif
deger = arizadan ONCE tespit (ne kadar erken), negatif = sonra.

Esik: ``tc`` (kritiklik sayaci) parametresi ile event-detected sayilan
ardisik 1 sayisi tanimlanir. Default 12 (paper'in tc/6 yerine, demo icin
daha duyarli — 12 timestep × 10 dk = 2 saat sustained alarm).

Onemli: tespit ARAMA penceresi sadece ``train_test == 'prediction'``
satirlaridir (paper kurali — training period model gozunde 'gorunmez',
oradaki tahmin kalibrasyonu icin reservedir; lead-time evaluation icin
kullanilmaz). Bu kural CSV ham siralamasini korur.

Kullanim::

    .venv/bin/python scripts/analyze_event_leadtime.py \\
        --datasets-dir _personal/wind_pivot/raw/CARE_To_Compare/Wind\\ Farm\\ A/datasets \\
        --event-info  _personal/wind_pivot/raw/CARE_To_Compare/Wind\\ Farm\\ A/event_info.csv \\
        --models-dir  data/models \\
        --event-id 0
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

# validate_models_on_care.py icindeki yardimcilara guven (ayni paket).
sys.path.insert(0, str(Path(__file__).parent))
from validate_models_on_care import (  # noqa: E402
    DatasetInfo,
    LoadedDataset,
    load_dataset,
    load_event_info,
    predictions_autoencoder,
    predictions_isolation_forest,
)

if TYPE_CHECKING:
    from numpy.typing import NDArray

logger = logging.getLogger("leadtime")

# 10 dakika aggregate adim — lead-time saat hesabi icin.
TIMESTEP_MINUTES = 10

# Default kritiklik sayaci esigi (timestep). 12 = 2 saat sustained alarm.
DEFAULT_TC = 12

# Default tarih kolon adi (Fraunhofer schema).
TIMESTAMP_COLUMN = "time_stamp"


@dataclass(frozen=True)
class LeadTimeRecord:
    """Tek event uzerinde tek engine'in lead-time sonucu."""

    event_id: int
    asset: str
    description: str
    engine: str
    detected: bool
    detection_idx: int | None         # Ilk sustained alarm index (CSV row)
    detection_timestamp: str | None   # Karsilik gelen timestamp (varsa)
    event_start_id: int
    event_start_timestamp: str | None
    lead_time_minutes: int | None     # event_start - detection (pozitif = erken)


def _first_sustained_alarm_idx(
    preds: NDArray[np.int_],
    tc: int,
    search_start: int = 0,
    search_end: int | None = None,
) -> int | None:
    """``[search_start, search_end)`` araliginda ilk ``tc`` ardisik 1'in indexini doner.

    Bu CARE 'kritiklik sayaci' yaklasiminin ilk-tetikleme noktasini bulan
    ucu — sustained alarm kavrami operatorel pratikle de uyumlu (bir-iki
    flicker FP'yi ayikla). ``search_start`` parametresi training period'u
    eval disinda tutmak icin kullanilir (paper kurali).
    """
    if tc <= 0:
        msg = f"tc pozitif olmali, geldi: {tc}"
        raise ValueError(msg)
    end = len(preds) if search_end is None else min(search_end, len(preds))
    streak = 0
    streak_start = -1
    for i in range(search_start, end):
        if int(preds[i]) == 1:
            if streak == 0:
                streak_start = i
            streak += 1
            if streak >= tc:
                return streak_start
        else:
            streak = 0
            streak_start = -1
    return None


def _read_first_prediction_idx(csv_path: Path) -> int | None:
    """``train_test == 'prediction'`` olan ilk satirin 0-indexli ID'sini doner.

    Paper konvansiyonu: model evaluation sadece prediction window'unda
    yapilir; training period kalibrasyon icindir. Bu helper window'in
    baslangicini bulur. Hicbir prediction satiri yoksa None.
    """
    if not csv_path.exists():
        return None
    with csv_path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter=";")
        for i, row in enumerate(reader):
            if row.get("train_test", "").strip() == "prediction":
                return i
    return None


def _read_timestamp_at(csv_path: Path, row_idx: int) -> str | None:
    """CSV'nin ``row_idx`` (0-indexed) satirinin timestamp'ini alir."""
    if not csv_path.exists():
        return None
    with csv_path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter=";")
        for i, row in enumerate(reader):
            if i == row_idx:
                return row.get(TIMESTAMP_COLUMN)
    return None


def _parse_timestamp(value: str | None) -> datetime | None:
    """ISO-like ``YYYY-MM-DD HH:MM:SS`` parse, hata → None."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.strip())
    except ValueError:
        return None


def analyze_event(
    info: DatasetInfo,
    ds: LoadedDataset,
    models_dir: Path,
    tc: int,
) -> list[LeadTimeRecord]:
    """Tek event uzerinde IF + AE icin lead-time hesapla."""
    if info.label != "anomaly":
        return []

    desc_extra = ""
    # Event description info objesinde yok; caller separate gecirebilir
    # ama bu helper'da bos birakiriz.

    engines: list[tuple[str, NDArray[np.int_] | None]] = [
        (
            "isolation_forest",
            predictions_isolation_forest(
                ds.features, models_dir, info.asset,
            ),
        ),
        (
            "autoencoder",
            predictions_autoencoder(
                ds.features, models_dir, info.asset,
            ),
        ),
    ]
    # Combined: bool OR (her ikisi de None ise None)
    if_p = engines[0][1]
    ae_p = engines[1][1]
    if if_p is None and ae_p is None:
        combined: NDArray[np.int_] | None = None
    elif if_p is None:
        combined = ae_p
    elif ae_p is None:
        combined = if_p
    else:
        combined = ((if_p.astype(bool)) | (ae_p.astype(bool))).astype(np.int_)
    engines.append(("combined", combined))

    event_ts = _read_timestamp_at(info.csv_path, info.event_start_id)
    event_dt = _parse_timestamp(event_ts)

    # Paper kurali: tespit araligi sadece prediction window. Eger prediction
    # rows yoksa (eski/manuel CSV), 0'dan baslar.
    pred_start = _read_first_prediction_idx(info.csv_path) or 0

    records: list[LeadTimeRecord] = []
    for engine_name, preds in engines:
        if preds is None:
            records.append(
                LeadTimeRecord(
                    event_id=info.event_id,
                    asset=info.asset,
                    description=desc_extra,
                    engine=engine_name,
                    detected=False,
                    detection_idx=None,
                    detection_timestamp=None,
                    event_start_id=info.event_start_id,
                    event_start_timestamp=event_ts,
                    lead_time_minutes=None,
                ),
            )
            continue
        idx = _first_sustained_alarm_idx(preds, tc, search_start=pred_start)
        if idx is None:
            records.append(
                LeadTimeRecord(
                    event_id=info.event_id,
                    asset=info.asset,
                    description=desc_extra,
                    engine=engine_name,
                    detected=False,
                    detection_idx=None,
                    detection_timestamp=None,
                    event_start_id=info.event_start_id,
                    event_start_timestamp=event_ts,
                    lead_time_minutes=None,
                ),
            )
            continue
        det_ts = _read_timestamp_at(info.csv_path, idx)
        det_dt = _parse_timestamp(det_ts)
        lead_min: int | None = None
        if event_dt and det_dt:
            lead_min = int((event_dt - det_dt).total_seconds() / 60)
        else:
            # Timestamp parse edilemediyse index farkindan tahmin
            lead_min = (info.event_start_id - idx) * TIMESTEP_MINUTES
        records.append(
            LeadTimeRecord(
                event_id=info.event_id,
                asset=info.asset,
                description=desc_extra,
                engine=engine_name,
                detected=True,
                detection_idx=idx,
                detection_timestamp=det_ts,
                event_start_id=info.event_start_id,
                event_start_timestamp=event_ts,
                lead_time_minutes=lead_min,
            ),
        )
    return records


def _format_lead(minutes: int | None) -> str:
    """Dakikayi okunur formata cevir: '8.4 saat once', '2.1 gun sonra', '—'."""
    if minutes is None:
        return "—"
    abs_m = abs(minutes)
    if abs_m < 60:
        s = f"{abs_m} dk"
    elif abs_m < 60 * 24:
        s = f"{abs_m / 60:.1f} sa"
    else:
        s = f"{abs_m / (60 * 24):.1f} gun"
    if minutes >= 0:
        return f"{s} once"
    return f"{s} sonra"


def render_markdown(records: list[LeadTimeRecord], tc: int) -> str:
    """Lead-time tablolarini markdown olarak doner."""
    if not records:
        return "_Hicbir anomaly event analiz edilmedi._\n"

    lines: list[str] = []
    lines.append(f"### Sustained-alarm esigi: tc = {tc} timestep "
                 f"({tc * TIMESTEP_MINUTES} dk = {tc * TIMESTEP_MINUTES / 60:.1f} sa)")
    lines.append("")
    lines.append(
        "| Event | Asset | Engine | Tespit | Tespit ts | Olay ts | Lead-time |",
    )
    lines.append("|------:|------:|--------|:------:|----------|---------|-----------|")
    for rec in records:
        det = "✅" if rec.detected else "❌"
        det_ts = rec.detection_timestamp or "—"
        ev_ts = rec.event_start_timestamp or "—"
        lead = _format_lead(rec.lead_time_minutes)
        lines.append(
            f"| {rec.event_id} | {rec.asset} | `{rec.engine}` | {det} | "
            f"{det_ts} | {ev_ts} | **{lead}** |",
        )
    return "\n".join(lines) + "\n"


def run(args: argparse.Namespace) -> int:
    """CLI runner."""
    event_info = Path(args.event_info)
    datasets_dir = Path(args.datasets_dir)
    models_dir = Path(args.models_dir)

    if not event_info.exists():
        logger.error("event_info.csv bulunamadi: %s", event_info)
        return 2

    infos = load_event_info(event_info)
    # Override CSV path to use --datasets-dir
    infos = [
        DatasetInfo(
            event_id=i.event_id,
            csv_path=datasets_dir / f"{i.event_id}.csv",
            label=i.label,
            event_start_id=i.event_start_id,
            event_end_id=i.event_end_id,
            asset=i.asset,
        )
        for i in infos
    ]

    if args.event_id is not None:
        infos = [i for i in infos if i.event_id == args.event_id]
    elif args.assets:
        wanted = {a.strip() for a in args.assets.split(",")}
        infos = [i for i in infos if i.asset in wanted]
    # Sadece anomaly event'ler
    infos = [i for i in infos if i.label == "anomaly"]
    if not infos:
        logger.error("Filtreden sonra anomaly event yok")
        return 1

    all_records: list[LeadTimeRecord] = []
    for info in infos:
        ds = load_dataset(info)
        if ds is None:
            logger.warning("Dataset yuklenemedi (skip): event=%d", info.event_id)
            continue
        records = analyze_event(info, ds, models_dir, args.tc)
        all_records.extend(records)

    output = render_markdown(all_records, args.tc)

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output, encoding="utf-8")
        logger.info("Cikti yazildi: %s", out_path)
    else:
        print(output)  # noqa: T201
    return 0


def build_argparser() -> argparse.ArgumentParser:
    """CLI argumanlari."""
    parser = argparse.ArgumentParser(
        description=(
            "Anomaly event'leri uzerinde model lead-time'i hesaplar "
            "(IF + AE + Combined)."
        ),
    )
    parser.add_argument("--event-info", required=True)
    parser.add_argument("--datasets-dir", required=True)
    parser.add_argument("--models-dir", default="data/models")
    parser.add_argument(
        "--event-id",
        type=int,
        default=None,
        help="Sadece tek event analiz et",
    )
    parser.add_argument(
        "--assets",
        default=None,
        help="Asset id filtresi (virgul ayirici); --event-id ile beraber kullanilmaz",
    )
    parser.add_argument(
        "--tc",
        type=int,
        default=DEFAULT_TC,
        help=f"Sustained alarm esigi (timestep). Default {DEFAULT_TC}.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Markdown rapor cikti yolu (verilmezse stdout)",
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
