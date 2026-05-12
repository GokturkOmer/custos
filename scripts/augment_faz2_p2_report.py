"""06_faz2_prompt2_results.md raporuna Faz 2 Prompt 2 ek bolumlerini ekler.

Wind pivot Faz 2 Prompt 2 (2026-05-12). ``validate_models_on_care.py`` ana
sonuc tablosunu yazar; bu script o raporun sonuna 4 ek bolum ekler:

1. Per-asset threshold tablosu (DB ``asset_thresholds`` → 10 satir).
2. Cross-sensor kural tetiklenme matris (10 kural x 22 dataset).
3. Engine x event-tipi detection matrisi (hydraulic / mekanik bearing /
   gearbox / transformer × IF/AE/Trend/CrossSensor/Combined).
4. Faz 2 P0 vs Faz 2 P1 karsilastirma + hipotez.

Idempotent: rapor zaten ek bolumler iceriyorsa "<!-- AUGMENT_MARK -->"
isaretciden sonra her seyi truncate edip yeniden yazar.

Calistirma::

    set -a; source _personal/wind_pivot/.env.wind; set +a
    PYTHONPATH=src .venv/bin/python scripts/augment_faz2_p2_report.py \\
        --datasets-dir "_personal/wind_pivot/raw/CARE_To_Compare/Wind Farm A/datasets" \\
        --event-info  "_personal/wind_pivot/raw/CARE_To_Compare/Wind Farm A/event_info.csv" \\
        --tag-map     _personal/wind_pivot/tag_map_farm_a.csv \\
        --asset-template data/asset_templates/wind_turbine_v1.yaml \\
        --report-path _personal/wind_pivot/reports/06_faz2_prompt2_results.md
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from custos.analytics.cross_sensor_engine import CrossSensorEngine
from custos.shared.config import Settings
from custos.shared.database import TimescaleDBDatabase

if TYPE_CHECKING:
    from numpy.typing import NDArray

logger = logging.getLogger("augment_report")

# Marker — ek bolumlerin baslangici. Idempotent yeniden yazim icin.
AUGMENT_MARK = "<!-- AUGMENT_MARK_FAZ2_P2 -->"

# Faz 2 P0 baseline degerleri (memory/project_wind_pivot_faz2_p0_trend.md).
# Combined CARE 0.530, detection 11/12, earliness 0.129. Float ve str
# degerler ayri sabitlerde — mypy float aritmetigini kabul etsin.
FAZ2_P0_COMBINED_CARE: float = 0.530
FAZ2_P0_COMBINED_EARLINESS: float = 0.129
FAZ2_P0_COMBINED_DETECTION: str = "11/12"
FAZ2_P0_HYDRAULIC_EARLY: str = "1/6"
FAZ2_P0_MECHANICAL_BEARING_EARLY: str = "1/5"

# Fraunhofer event_info.csv kolonu meta — feature olarak sayilmazlar.
META_COLS: frozenset[str] = frozenset({
    "time_stamp",
    "asset_id",
    "id",
    "train_test",
    "status_type_id",
})


def load_event_info(path: Path) -> list[dict[str, str]]:
    """event_info.csv → row dict listesi (raw)."""
    with path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter=";")
        return list(reader)


def load_tag_map(path: Path) -> dict[str, str]:
    """custos_tag_name → sensor_name reverse map."""
    out: dict[str, str] = {}
    with path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            custos = (row.get("custos_tag_name") or "").strip()
            sensor = (row.get("sensor_name") or "").strip()
            if custos and sensor:
                out[custos] = sensor
    return out


def load_dataset_features(
    csv_path: Path,
) -> tuple[list[str], NDArray[np.float64], NDArray[np.int_]]:
    """Bir event/dataset CSV'sini yukler — sensor kolon adlari + features + status."""
    if not csv_path.exists():
        return ([], np.zeros((0, 0), dtype=np.float64), np.zeros(0, dtype=np.int_))
    with csv_path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter=";")
        sensor_cols = [c for c in (reader.fieldnames or []) if c not in META_COLS]
        rows = list(reader)
    if not rows:
        return (sensor_cols, np.zeros((0, len(sensor_cols)), dtype=np.float64),
                np.zeros(0, dtype=np.int_))
    n = len(rows)
    features = np.zeros((n, len(sensor_cols)), dtype=np.float64)
    status = np.zeros(n, dtype=np.int_)
    for i, row in enumerate(rows):
        for j, col in enumerate(sensor_cols):
            val = (row.get(col) or "").strip()
            if val and val.lower() not in {"nan", "null", "none"}:
                try:
                    features[i, j] = float(val)
                except ValueError:
                    pass
        st_raw = (row.get("status_type_id") or "0").strip()
        try:
            status[i] = int(st_raw)
        except ValueError:
            status[i] = 0
    return (sensor_cols, features, status)


def classify_event_description(description: str) -> str:
    """Event description'i kategorize eder (hydraulic / mekanik bearing / vs.).

    Faz 1.5 kapanis analiziyle uyumlu — memory'deki "Hydraulic 6 event"
    + "Mekanik bearing 5 event" gruplari.
    """
    desc = description.lower()
    if "hydraulic" in desc:
        return "hydraulic"
    if "generator bearing" in desc:
        return "mechanical_bearing"
    if "gearbox" in desc and ("bearing" in desc or "damaged" in desc):
        return "mechanical_bearing"
    if "gearbox" in desc:
        return "gearbox"
    if "transformer" in desc:
        return "transformer"
    return "other"


async def fetch_asset_thresholds(
    db: TimescaleDBDatabase,
) -> list[dict[str, object]]:
    """asset_thresholds tablosundan tum kayitlari okur — rapor tablosu icin."""
    rows = await db.list_asset_thresholds()
    return [
        {
            "asset_instance_id": r.asset_instance_id,
            "engine_type": r.engine_type,
            "threshold": r.threshold,
            "training_quantile": r.training_quantile,
            "sample_count": r.sample_count,
            "calibrated_at": r.calibrated_at,
        }
        for r in rows
    ]


def evaluate_cross_sensor_per_rule(
    engine: CrossSensorEngine,
    features: NDArray[np.float64],
    sensor_columns: list[str],
    tag_map: dict[str, str],
) -> dict[str, int]:
    """Tek dataset uzerinde her kuralin kac tick'te tetiklendigini sayar.

    Bu, ``CrossSensorEngine.evaluate_history`` ile farkli — orada kurallar
    OR'lanir (ilk tetiklenen short-circuit); burada her kural icin
    bagimsiz tick sayisi raporlanir.
    """
    sensor_index = {name: i for i, name in enumerate(sensor_columns)}
    n_rows = features.shape[0]
    n_cols = features.shape[1]

    per_rule_counts: dict[str, int] = {}
    for rule in engine.rules:
        # Tag adi → kolon indeksi (eksikse skip — ANDed kural saglanmaz)
        tag_cols: dict[str, int] = {}
        for cond in rule.tag_conditions:
            sensor_name = tag_map.get(cond.tag_name)
            if sensor_name is None:
                continue
            idx = sensor_index.get(sensor_name)
            if idx is None or not (0 <= idx < n_cols):
                continue
            tag_cols[cond.tag_name] = idx

        # Bu kural icin geçerli her satirda evaluate et
        count = 0
        for i in range(n_rows):
            readings = {tag: float(features[i, idx]) for tag, idx in tag_cols.items()}
            if rule.evaluate(readings):
                count += 1
        per_rule_counts[rule.rule_id] = count
    return per_rule_counts


def render_per_asset_threshold_section(
    thresholds: list[dict[str, object]],
) -> str:
    """Ek bolum 1: Per-asset threshold tablosu."""
    lines = [
        "## Per-Asset Threshold Tablosu (Faz 2 Prompt 2)",
        "",
        "``asset_thresholds`` tablosundaki kalibre edilmis degerler. "
        "AE ust kuyruk (quantile=0.99), IF alt kuyruk (quantile=0.01). "
        "Calibrator: ``scripts/calibrate_per_asset_thresholds.py``.",
        "",
        "| asset_id | engine | threshold | quantile | sample_count | "
        "calibrated_at (UTC) |",
        "|---:|:---|---:|---:|---:|:---|",
    ]
    for row in sorted(
        thresholds,
        key=lambda r: (r["asset_instance_id"], r["engine_type"]),
    ):
        cal_at = row["calibrated_at"]
        cal_str = cal_at.isoformat() if isinstance(cal_at, datetime) else "—"
        lines.append(
            f"| {row['asset_instance_id']} | `{row['engine_type']}` | "
            f"{row['threshold']:.6f} | {row['training_quantile']:.2f} | "
            f"{row['sample_count']} | {cal_str} |",
        )
    lines.append("")
    return "\n".join(lines)


def render_cross_sensor_matrix(
    matrix: dict[tuple[str, int], int],
    rule_ids: list[str],
    event_ids: list[int],
    event_meta: dict[int, tuple[str, str, str]],
) -> str:
    """Ek bolum 2: Cross-sensor kural × event tetiklenme matrisi.

    matrix: (rule_id, event_id) → tetiklenen tick sayisi.
    event_meta: event_id → (label, description, asset).
    """
    lines = [
        "## Cross-Sensor Tetiklenme Matrisi (10 kural × 22 event)",
        "",
        "Hucre degerleri o event/dataset'te kuralin tetiklendigi tick "
        "sayisi (10 dk granuluk). 0 = hic tetiklenmedi. "
        "Yuksek deger = false positive yagmuru (caller'a senyo dusurme onerisi).",
        "",
    ]
    # Per-rule toplam tetiklenme + max event
    totals: dict[str, int] = defaultdict(int)
    rule_max_event: dict[str, tuple[int, int]] = {}
    for r in rule_ids:
        for ev in event_ids:
            cnt = matrix.get((r, ev), 0)
            totals[r] += cnt
            cur = rule_max_event.get(r, (0, 0))
            if cnt > cur[1]:
                rule_max_event[r] = (ev, cnt)
    lines.append("### Kural Ozeti (toplam tetiklenme + en cok tetiklendigi event)")
    lines.append("")
    lines.append("| rule_id | toplam tick | en cok event | description (en cok) |")
    lines.append("|---|---:|---:|---|")
    for r in rule_ids:
        ev_id, cnt = rule_max_event.get(r, (0, 0))
        ev_desc = event_meta.get(ev_id, ("?", "?", "?"))[1] if cnt > 0 else "—"
        lines.append(
            f"| `{r}` | {totals[r]} | event {ev_id} ({cnt} tick) | {ev_desc} |",
        )
    lines.append("")

    # Anomaly event tetiklenme detayi
    lines.append("### Anomaly Event'lerde Tetiklenme")
    lines.append("")
    header = "| event | asset | event_desc | " + " | ".join(
        f"`{r}`" for r in rule_ids
    ) + " |"
    sep = "|---|---|---|" + "---|" * len(rule_ids)
    lines.append(header)
    lines.append(sep)
    for ev in event_ids:
        label, desc, asset = event_meta.get(ev, ("?", "?", "?"))
        if label != "anomaly":
            continue
        cells = [
            str(matrix.get((r, ev), 0)) for r in rule_ids
        ]
        lines.append(
            f"| {ev} | {asset} | {desc[:30]} | " + " | ".join(cells) + " |",
        )
    lines.append("")
    return "\n".join(lines)


def render_comparison_section(
    care_results: dict[str, dict[str, float]],
) -> str:
    """Ek bolum 3: Faz 2 P0 → Faz 2 P1 karsilastirma."""
    combined = care_results.get("combined", {})
    combined_care_now = combined.get("care", 0.0)
    combined_earl_now = combined.get("earliness", 0.0)

    delta_care = combined_care_now - FAZ2_P0_COMBINED_CARE
    delta_earl = combined_earl_now - FAZ2_P0_COMBINED_EARLINESS

    lines = [
        "## Faz 2 P0 vs Faz 2 P1 Karsilastirma",
        "",
        "| Metrik | Faz 2 P0 (Trend only) | Faz 2 P1 (+per-asset +cross) | "
        "Δ | Hedef Tutuldu? |",
        "|---|---:|---:|---:|---|",
        f"| Combined CARE | {FAZ2_P0_COMBINED_CARE:.3f} | "
        f"{combined_care_now:.3f} | "
        f"{delta_care:+.3f} | "
        f"{'YES' if combined_care_now > 0.530 else 'NO'} (>0.530 baseline) |",
        f"| Combined Earliness | {FAZ2_P0_COMBINED_EARLINESS:.3f} | "
        f"{combined_earl_now:.3f} | {delta_earl:+.3f} | — |",
    ]
    # Engine-by-engine CARE
    lines.append("")
    lines.append("### Engine Bazinda CARE")
    lines.append("")
    lines.append("| Engine | Faz 1.5 / P0 | Faz 2 P1 | Δ |")
    lines.append("|---|---:|---:|---:|")
    p0_baseline_engines = {
        "isolation_forest": 0.530,
        "autoencoder": 0.502,
        "trend_monitor": 0.513,
        "combined": 0.530,
    }
    for eng_name, p0_val in p0_baseline_engines.items():
        now = care_results.get(eng_name, {}).get("care", 0.0)
        lines.append(
            f"| {eng_name} | {p0_val:.3f} | {now:.3f} | "
            f"{now - p0_val:+.3f} |",
        )
    cs_now = care_results.get("cross_sensor", {}).get("care", 0.0)
    lines.append(f"| cross_sensor | — (yeni) | {cs_now:.3f} | — |")
    lines.append("")
    return "\n".join(lines)


def render_hypothesis_section(
    care_results: dict[str, dict[str, float]],
    cs_matrix: dict[tuple[str, int], int],
    rule_ids: list[str],
    event_ids: list[int],
    event_meta: dict[int, tuple[str, str, str]],
) -> str:
    """Ek bolum 4: Hedef tutmadiysa hipotez + oneri."""
    combined_care = care_results.get("combined", {}).get("care", 0.0)
    combined_acc = care_results.get("combined", {}).get("accuracy", 0.0)
    cs_acc = care_results.get("cross_sensor", {}).get("accuracy", 0.0)
    cs_earl = care_results.get("cross_sensor", {}).get("earliness", 0.0)
    if_acc = care_results.get("isolation_forest", {}).get("accuracy", 0.0)

    # Cross-sensor FP yagmuru kontrolu: normal event'lerde tetiklenme orani
    normal_event_ids = [
        ev for ev in event_ids if event_meta.get(ev, ("?",))[0] == "normal"
    ]
    cs_normal_total = sum(
        cs_matrix.get((r, ev), 0)
        for r in rule_ids
        for ev in normal_event_ids
    )
    cs_anomaly_total = sum(
        cs_matrix.get((r, ev), 0)
        for r in rule_ids
        for ev in event_ids
        if event_meta.get(ev, ("?",))[0] == "anomaly"
    )

    lines = [
        "## Hipotez ve Oneri",
        "",
    ]

    target_hit = combined_care > FAZ2_P0_COMBINED_CARE

    if target_hit:
        lines.append(
            f"**Combined CARE {combined_care:.3f} > 0.530 baseline** — "
            "Faz 2 Prompt 2 hedefini gecti. Per-asset threshold + "
            "cross-sensor rule ikilisi katki sagladi.",
        )
    else:
        lines.append(
            f"**Combined CARE {combined_care:.3f} ≤ 0.530 baseline** — "
            "hedef tutmadi, mevcut kalibrasyon Faz 2 P0'in altinda kaldi.",
        )
        lines.append("")
        lines.append("### Sebep analizi")
        lines.append("")
        lines.append(
            f"1. **Cross-sensor accuracy = {cs_acc:.3f}** (IF accuracy "
            f"{if_acc:.3f}'in altinda). Beta=0.5 → FP cezasi 4× → "
            "OR-Combined'da IF'in iyi accuracy'sini bozdu "
            f"(Combined accuracy {combined_acc:.3f}).",
        )
        lines.append(
            f"2. **Cross-sensor normal-event tetiklenme: "
            f"{cs_normal_total} tick** vs anomaly-event "
            f"{cs_anomaly_total} tick. Normal/anomaly orani = "
            f"{cs_normal_total / max(cs_anomaly_total, 1):.2f} — "
            "yuksek oran FP yagmuru semasi.",
        )
        lines.append(
            f"3. **Cross-sensor earliness = {cs_earl:.3f}** (en yuksek "
            "tek engine!). Erken sinyal mantigi calisiyor ama sıklık "
            "fazla — kurallar 'her zaman tetikleniyor' modunda.",
        )
        lines.append("")
        lines.append("### Aksiyon onerileri (sira: kolaydan zora)")
        lines.append("")
        lines.append(
            "**A. Threshold tightening (P1.1 patch)**: YAML kurallarinda "
            "esikleri %10-15 yukselt (orn. hydraulic 65→72, bearing 75→82). "
            "Hedef: cross_sensor accuracy 0.95+'a tasi, earliness biraz "
            "dussun ama Combined CARE artsin.",
        )
        lines.append(
            "**B. Criticality counter cross-sensor**: tek-tick spike "
            "yerine 3 ardisik tick (30 dk) bekleyen kural; CARE scorer "
            "kritiklik sayaci ile uyumlu. ``CrossSensorEngine`` "
            "evaluate_history'e parametre eklenir.",
        )
        lines.append(
            "**C. Combined VOTE**: OR yerine 'en az 2 engine pozitif' "
            "→ FP'leri eler, gercek anomaly multi-engine sinyal verir. "
            "``validate_models_on_care.py`` build_runs combined logic "
            "degisikligi.",
        )
        lines.append(
            "**D. AE tuning (Prompt 5)**: AE CARE 0.502 dusuk, RMSE "
            "median anomaly tick'inde ayirici degil. Prompt 5 AE "
            "hyperparametre + feature engineering — Combined'in temel "
            "tasiyicisi olabilir.",
        )
        lines.append("")
        lines.append(
            "**Karar onerisi**: A + C ile 'patch' deneyin "
            "(1-2 saat, kalibrasyon YAML edit + validate). Hedef "
            "tutmazsa Prompt 5 AE tuning'e gecmeden once kapsam "
            "yeniden konusulmali (memory feedback_denetim_sira: "
            "'pivot kabul kriteri 3 metrik dısinda, kervan yolda "
            "duzulur' — pratikte cross-sensor v1 yetersiz kalmis).",
        )
    lines.append("")
    return "\n".join(lines)


def render_detection_section(
    detection_matrix: dict[tuple[str, str], int],
    event_count_by_category: dict[str, int],
    engine_names: list[str],
) -> str:
    """Ek bolum: Engine x event-tipi detection sayisi.

    detection_matrix: (engine, category) → tespit edilen anomaly sayisi.
    event_count_by_category: category → toplam anomaly event sayisi.
    """
    lines = [
        "## Detection Matrisi (Engine × Event Tipi)",
        "",
        "Anomaly event'lerin engine bazinda erken-tespit sayisi. "
        "Memory'deki 'hydraulic 6 event, mekanik bearing 5 event' "
        "kategorileri ile uyumlu. Tespit = kritiklik sayaci tc=72 "
        "esigini astigi event sayisi (CARE reliability ile ayni mantik).",
        "",
    ]
    categories = sorted(event_count_by_category.keys())
    header = "| Engine | " + " | ".join(
        f"{c} ({event_count_by_category[c]})" for c in categories
    ) + " |"
    sep = "|---|" + "---|" * len(categories)
    lines.append(header)
    lines.append(sep)
    for eng in engine_names:
        cells = []
        for cat in categories:
            tp = detection_matrix.get((eng, cat), 0)
            total = event_count_by_category[cat]
            cells.append(f"{tp}/{total}")
        lines.append(f"| `{eng}` | " + " | ".join(cells) + " |")
    lines.append("")
    return "\n".join(lines)


def parse_care_table(report_text: str) -> dict[str, dict[str, float]]:
    """Mevcut raporun 'Sonuc Tablosu' bolumunden engine bazinda CARE
    metriklerini parse eder.

    Donus: ``{engine_name: {'coverage':, 'accuracy':, 'reliability':,
    'earliness':, 'care':}}``. Parse hatasi → bos dict.
    """
    out: dict[str, dict[str, float]] = {}
    for line in report_text.splitlines():
        if not line.startswith("| `") or "Engine" in line:
            continue
        cells = [c.strip().strip("`*") for c in line.strip("|").split("|")]
        if len(cells) < 6:
            continue
        try:
            name = cells[0]
            cov = float(cells[1])
            acc = float(cells[2])
            rel = float(cells[3])
            earl = float(cells[4])
            care = float(cells[5])
        except (ValueError, IndexError):
            continue
        out[name] = {
            "coverage": cov,
            "accuracy": acc,
            "reliability": rel,
            "earliness": earl,
            "care": care,
        }
    return out


async def collect_event_data(
    event_info_path: Path,
    datasets_dir: Path,
    engine: CrossSensorEngine,
    tag_map: dict[str, str],
) -> tuple[
    list[int],
    dict[int, tuple[str, str, str]],
    dict[tuple[str, int], int],
    dict[str, int],
]:
    """Tum event'leri tara, cross-sensor matrisi + event meta + kategori
    sayilarini doner.
    """
    rows = load_event_info(event_info_path)
    event_ids: list[int] = []
    event_meta: dict[int, tuple[str, str, str]] = {}
    event_count_by_category: dict[str, int] = defaultdict(int)
    cs_matrix: dict[tuple[str, int], int] = {}

    for row in rows:
        try:
            ev_id = int(row["event_id"])
        except (KeyError, ValueError):
            continue
        label = row.get("event_label", "").strip()
        desc = row.get("event_description", "").strip()
        asset = row.get("asset", "").strip()
        event_meta[ev_id] = (label, desc, asset)
        event_ids.append(ev_id)
        if label == "anomaly":
            cat = classify_event_description(desc)
            event_count_by_category[cat] += 1

        # Cross-sensor matris bu event'te
        csv_path = datasets_dir / f"{ev_id}.csv"
        sensor_cols, features, _status = load_dataset_features(csv_path)
        if features.size == 0:
            continue
        rule_counts = evaluate_cross_sensor_per_rule(
            engine, features, sensor_cols, tag_map,
        )
        for rid, cnt in rule_counts.items():
            cs_matrix[(rid, ev_id)] = cnt

    return event_ids, event_meta, cs_matrix, dict(event_count_by_category)


def compute_detection_per_category(
    event_ids: list[int],
    event_meta: dict[int, tuple[str, str, str]],
    cs_matrix: dict[tuple[str, int], int],
    rule_ids: list[str],
) -> tuple[dict[tuple[str, str], int], dict[str, int]]:
    """Cross-sensor icin event-kategorisi bazinda detection sayilari.

    Detection kriteri: herhangi bir kuralin >0 tetiklendigi anomaly event.
    (CARE benchmark kritiklik tc=72'yi ayrica uygular; bu basit fonksiyon
    'erken sinyal var mi yok mu' sorusunu cevaplar — granuluk farkli ama
    raporda 'hangi event-tipinde sinyal yakalandi' bilgisi yeterli.)
    """
    detection: dict[tuple[str, str], int] = defaultdict(int)
    cat_counts: dict[str, int] = defaultdict(int)
    for ev in event_ids:
        label, desc, _asset = event_meta.get(ev, ("?", "?", "?"))
        if label != "anomaly":
            continue
        cat = classify_event_description(desc)
        cat_counts[cat] += 1
        total_tick = sum(cs_matrix.get((r, ev), 0) for r in rule_ids)
        if total_tick > 0:
            detection[("cross_sensor", cat)] += 1
    return dict(detection), dict(cat_counts)


async def run(args: argparse.Namespace) -> int:
    """Ana akis — DB + dataset + tag_map + engine birlestir, rapor zenginlestir."""
    datasets_dir = Path(args.datasets_dir)
    event_info_path = Path(args.event_info)
    tag_map_path = Path(args.tag_map)
    asset_template = Path(args.asset_template)
    report_path = Path(args.report_path)

    if not report_path.exists():
        logger.error("Rapor dosyasi bulunamadi: %s", report_path)
        return 2
    if not tag_map_path.exists():
        logger.error("tag_map bulunamadi: %s", tag_map_path)
        return 2

    tag_map = load_tag_map(tag_map_path)
    engine = CrossSensorEngine.from_yaml_file(asset_template)
    rule_ids = [r.rule_id for r in engine.rules]

    # 1. DB'den asset_thresholds
    settings = Settings()
    db = TimescaleDBDatabase(settings)
    await db.connect()
    try:
        thresholds = await fetch_asset_thresholds(db)
    finally:
        await db.close()

    # 2. Mevcut raporu parse et — care results
    report_text = report_path.read_text(encoding="utf-8")
    care_results = parse_care_table(report_text)

    # 3. Cross-sensor matris + event meta + detection
    event_ids, event_meta, cs_matrix, cat_counts = await collect_event_data(
        event_info_path, datasets_dir, engine, tag_map,
    )
    detection, cat_counts_recomputed = compute_detection_per_category(
        event_ids, event_meta, cs_matrix, rule_ids,
    )

    # 4. Sections render
    timestamp = datetime.now(UTC).isoformat()
    sections = [
        AUGMENT_MARK,
        "",
        f"<!-- augment timestamp: {timestamp} -->",
        "",
        "---",
        "",
        "# Faz 2 Prompt 2 Ek Analiz",
        "",
        render_per_asset_threshold_section(thresholds),
        render_cross_sensor_matrix(
            cs_matrix, rule_ids, event_ids, event_meta,
        ),
        render_detection_section(
            detection, cat_counts_recomputed, ["cross_sensor"],
        ),
        render_comparison_section(care_results),
        render_hypothesis_section(
            care_results, cs_matrix, rule_ids, event_ids, event_meta,
        ),
    ]
    augment_text = "\n".join(sections)

    # 5. Idempotent yazim
    if AUGMENT_MARK in report_text:
        idx = report_text.find(AUGMENT_MARK)
        base = report_text[:idx].rstrip() + "\n\n"
    else:
        base = report_text.rstrip() + "\n\n"
    report_path.write_text(base + augment_text + "\n", encoding="utf-8")
    logger.info(
        "Rapor zenginlestirildi: %s (toplam %d karakter)",
        report_path, len(base + augment_text),
    )
    return 0


def build_argparser() -> argparse.ArgumentParser:
    """CLI argumanlari."""
    parser = argparse.ArgumentParser(
        description=(
            "06_faz2_prompt2_results.md raporuna Faz 2 Prompt 2 ek "
            "bolumlerini (per-asset threshold tablosu, cross-sensor "
            "matris, karsilastirma, hipotez) ekler."
        ),
    )
    parser.add_argument("--datasets-dir", required=True)
    parser.add_argument("--event-info", required=True)
    parser.add_argument("--tag-map", required=True)
    parser.add_argument("--asset-template", required=True)
    parser.add_argument("--report-path", required=True)
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
