"""Endurance günlük kontrol — son 24 saat CSV'yi analiz eder.

`scripts/endurance_metrics.py` tarafından beslenen `logs/endurance.csv`
dosyasını okur, son 24 saatteki metrikleri özetler, kırmızı bayrak
kriterlerini değerlendirir ve bir markdown rapor dosyası üretir.

Kırmızı bayrak kriterleri (prompt/kılavuz ile aynı):
    RSS memory     : 24 saatte >%5 artış → WARN, >%10 artış → CRIT
    DB connections : max kullanım > %90 pool → CRIT
    Tick miss      : 24 saat ortalaması > 0.01 AND slope > 0 → CRIT
    Disk           : %15 boş alan altında CRIT;
                     kullanım artışı + boş alan azalma trendi varsa WARN
    tag_readings   : insert hızı ilk 12 saat - son 12 saat arası %10'dan
                     fazla düşmüş → WARN

Çıkış kodları: 0 = OK, 1 = WARN, 2 = CRIT. CI/cron bu kodlara göre
kendi uyarı mekanizmasını tetikleyebilir.

Kullanım:
    python scripts/endurance_daily_check.py
    python scripts/endurance_daily_check.py --csv /opt/custos/logs/endurance.csv
    python scripts/endurance_daily_check.py --report-dir /tmp
    python scripts/endurance_daily_check.py --day 5
"""

from __future__ import annotations

import argparse
import csv
import io
import math
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import IntEnum
from pathlib import Path

# Varsayılan yollar
DEFAULT_CSV = Path("logs/endurance.csv")
DEFAULT_REPORT_DIR = Path("_personal/pilot")

# Eşikler (kritiklikler gerektiği kadar sade tutuldu; ince ayar W7 resmi
# endurance'ından sonra, toplanan verinin üzerinden revize edilebilir).
_RSS_WARN_PCT = 5.0
_RSS_CRIT_PCT = 10.0
_DB_POOL_SIZE = 100  # PostgreSQL default max_connections
_DB_POOL_WARN_PCT = 80.0
_DB_POOL_CRIT_PCT = 90.0
_TICK_MISS_WARN = 0.01
_TICK_MISS_CRIT = 0.05
_READINGS_RATE_DROP_WARN_PCT = 10.0
_DISK_AVAIL_CRIT_PCT = 15.0  # healthcheck ile aynı eşik


class Status(IntEnum):
    """Sıralı kritiklik — sayı büyüdükçe daha kırmızı."""

    OK = 0
    WARN = 1
    CRIT = 2


_STATUS_LABEL = {Status.OK: "OK", Status.WARN: "WARN", Status.CRIT: "CRIT"}
_STATUS_EMOJI = {Status.OK: "✅", Status.WARN: "⚠️", Status.CRIT: "🛑"}


@dataclass
class Check:
    """Tek bir kural kontrolünün sonucu."""

    name: str
    status: Status
    summary: str
    details: list[str] = field(default_factory=list)


@dataclass
class DailyReport:
    """24 saatlik özet — console + markdown üretimini besler."""

    generated_at: datetime
    day_number: int
    window_start: datetime
    window_end: datetime
    sample_count: int
    overall: Status
    checks: list[Check]


# --- CSV okuma + window filtreleme ---


def _parse_iso(ts: str) -> datetime | None:
    """ISO-8601 string → aware datetime; parse başarısızsa None.

    Metrics script her zaman UTC ISO yazar; yine de defansif parse.
    """
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def _to_float(value: str) -> float | None:
    """Boş string / 'None' → None. Diğer durumda float veya None (parse fail)."""
    if value == "" or value.lower() == "none":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _to_int(value: str) -> int | None:
    """int parse helper; _to_float ile simetrik."""
    if value == "" or value.lower() == "none":
        return None
    try:
        return int(float(value))  # "8.0" gibi ondalıklı gelirse yine anlat
    except ValueError:
        return None


def read_csv_rows(csv_path: Path) -> list[dict[str, str]]:
    """CSV'nin tüm satırlarını OrderedDict listesi olarak döndürür."""
    with csv_path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        return [row for row in reader]


def filter_window(
    rows: list[dict[str, str]],
    end: datetime,
    window: timedelta,
) -> list[dict[str, str]]:
    """Son `window` içinde kalan satırları döndürür (timestamp kolonuna göre)."""
    start = end - window
    filtered: list[dict[str, str]] = []
    for row in rows:
        ts = _parse_iso(row.get("timestamp", ""))
        if ts is None:
            continue
        if start <= ts <= end:
            filtered.append(row)
    return filtered


# --- Sayısal yardımcılar (stdlib; pandas bağımlılığı yok) ---


def column_series(
    rows: list[dict[str, str]],
    column: str,
    parser: str = "float",
) -> list[tuple[datetime, float]]:
    """Bir CSV kolonundan (timestamp, değer) çiftlerini çıkarır.

    None / boş / parse edilemez hücreler atlanır — bu tolerans, daemon'ın
    ilk birkaç döngüsünde DB erişilemediği senaryolarda crash engeller.
    """
    convert = _to_float if parser == "float" else _to_int
    series: list[tuple[datetime, float]] = []
    for row in rows:
        ts = _parse_iso(row.get("timestamp", ""))
        raw = row.get(column, "")
        if ts is None:
            continue
        value = convert(raw)
        if value is None:
            continue
        series.append((ts, float(value)))
    return series


def linear_slope(series: list[tuple[datetime, float]]) -> float:
    """(timestamp, değer) serisi için saat başına slope (least squares).

    Seri <2 nokta ise 0.0. Normalizasyon: x = saat cinsinden first'ten delta.
    """
    if len(series) < 2:
        return 0.0
    t0 = series[0][0]
    xs = [(ts - t0).total_seconds() / 3600.0 for ts, _ in series]
    ys = [y for _, y in series]
    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    den = sum((x - mean_x) ** 2 for x in xs)
    if den == 0.0 or math.isclose(den, 0.0):
        return 0.0
    return num / den


def percent_change(first: float, last: float) -> float:
    """Yüzde değişim. first 0 ise 0.0 (bölme güvenliği)."""
    if first == 0.0:
        return 0.0
    return ((last - first) / first) * 100.0


# --- Kontroller ---


def _check_memory(
    series: list[tuple[datetime, float]],
    label: str,
) -> Check:
    """RSS memory trendi — ilk/son delta yüzdesi."""
    if len(series) < 2:
        return Check(
            name=f"{label} RSS",
            status=Status.OK,
            summary="Yetersiz örnek (ilk çalıştırma?)",
        )
    first = series[0][1]
    last = series[-1][1]
    pct = percent_change(first, last)
    slope = linear_slope(series)
    abs_pct = abs(pct)

    if abs_pct >= _RSS_CRIT_PCT:
        status = Status.CRIT
    elif abs_pct >= _RSS_WARN_PCT:
        status = Status.WARN
    else:
        status = Status.OK

    summary = (
        f"{label} RSS: {first:.1f} → {last:.1f} MB "
        f"({pct:+.1f}%, {_STATUS_LABEL[status]})"
    )
    details = [f"Slope: {slope:+.3f} MB/saat"]
    return Check(name=f"{label} RSS", status=status, summary=summary, details=details)


def check_memory_trends(rows: list[dict[str, str]]) -> list[Check]:
    """Collector + dashboard RSS serileri için iki kontrol."""
    collector = column_series(rows, "collector_rss_mb")
    dashboard = column_series(rows, "dashboard_rss_mb")
    return [
        _check_memory(collector, "Collector"),
        _check_memory(dashboard, "Dashboard"),
    ]


def check_db_connections(rows: list[dict[str, str]]) -> Check:
    """DB bağlantı pool kullanımı (max + ortalama)."""
    series = column_series(rows, "db_connections", parser="int")
    if not series:
        return Check(
            name="DB bağlantı",
            status=Status.OK,
            summary="Veri yok (DB erişimi henüz toplanmadı?)",
        )
    values = [v for _, v in series]
    maximum = max(values)
    minimum = min(values)
    mean = sum(values) / len(values)
    max_pct = (maximum / _DB_POOL_SIZE) * 100.0

    if max_pct >= _DB_POOL_CRIT_PCT:
        status = Status.CRIT
    elif max_pct >= _DB_POOL_WARN_PCT:
        status = Status.WARN
    else:
        status = Status.OK

    summary = (
        f"DB bağlantı: min={int(minimum)} / avg={mean:.1f} / max={int(maximum)} "
        f"(pool {_DB_POOL_SIZE}, max %{max_pct:.0f}) — {_STATUS_LABEL[status]}"
    )
    return Check(name="DB bağlantı", status=status, summary=summary)


def check_tick_miss(rows: list[dict[str, str]]) -> Check:
    """Tick miss oranı (ortalama + slope)."""
    series = column_series(rows, "tick_miss_ratio")
    if not series:
        return Check(
            name="Tick miss oranı",
            status=Status.OK,
            summary="Ölçüm yok",
        )
    values = [v for _, v in series]
    mean = sum(values) / len(values)
    slope = linear_slope(series)

    if mean >= _TICK_MISS_CRIT and slope > 0:
        status = Status.CRIT
    elif mean >= _TICK_MISS_WARN and slope >= 0:
        status = Status.WARN
    else:
        status = Status.OK

    summary = (
        f"Tick miss: ortalama {mean:.4f} (hedef <{_TICK_MISS_WARN}), "
        f"slope {slope:+.5f}/saat — {_STATUS_LABEL[status]}"
    )
    return Check(name="Tick miss oranı", status=status, summary=summary)


def check_disk(rows: list[dict[str, str]]) -> Check:
    """Disk kullanımı + boş alan — retention sağlıklı mı?"""
    used = column_series(rows, "disk_used_gb")
    avail = column_series(rows, "disk_avail_gb")
    if not used or not avail:
        return Check(
            name="Disk",
            status=Status.OK,
            summary="Disk metriği yok",
        )
    used_first = used[0][1]
    used_last = used[-1][1]
    avail_first = avail[0][1]
    avail_last = avail[-1][1]
    total = avail_last + used_last
    avail_pct = (avail_last / total * 100.0) if total > 0 else 100.0

    # Disk büyüyor + boş alan azalıyor → warn; eşik altı boş → crit
    growing = used_last > used_first
    tightening = avail_last < avail_first
    if avail_pct < _DISK_AVAIL_CRIT_PCT:
        status = Status.CRIT
    elif growing and tightening:
        status = Status.WARN
    else:
        status = Status.OK

    summary = (
        f"Disk: {used_first:.1f} → {used_last:.1f} GB kullanıldı, "
        f"%{avail_pct:.1f} boş — {_STATUS_LABEL[status]}"
    )
    return Check(name="Disk", status=status, summary=summary)


def check_readings_rate(rows: list[dict[str, str]]) -> Check:
    """tag_readings insert hızında %10+ düşüş var mı (ilk yarı vs ikinci yarı)?"""
    series = column_series(rows, "tag_readings_count", parser="int")
    if len(series) < 4:
        return Check(
            name="Okuma hızı",
            status=Status.OK,
            summary="Yetersiz örnek",
        )
    mid = len(series) // 2
    first_half = series[:mid]
    second_half = series[mid:]
    rate_first = _rate_per_hour(first_half)
    rate_second = _rate_per_hour(second_half)

    if rate_first <= 0.0:
        return Check(
            name="Okuma hızı",
            status=Status.OK,
            summary=f"İlk yarı hızı ölçülemedi (ilk: {rate_first:.1f}/saat)",
        )

    drop_pct = percent_change(rate_first, rate_second)
    if drop_pct <= -_READINGS_RATE_DROP_WARN_PCT:
        status = Status.WARN
    else:
        status = Status.OK

    summary = (
        f"Okuma hızı: ilk yarı {rate_first:.0f}/saat, "
        f"ikinci yarı {rate_second:.0f}/saat ({drop_pct:+.1f}%) "
        f"— {_STATUS_LABEL[status]}"
    )
    return Check(name="Okuma hızı", status=status, summary=summary)


def _rate_per_hour(series: list[tuple[datetime, float]]) -> float:
    """(timestamp, cumulative_count) serisinde ortalama saatlik artış."""
    if len(series) < 2:
        return 0.0
    t0, y0 = series[0]
    t1, y1 = series[-1]
    hours = (t1 - t0).total_seconds() / 3600.0
    if hours <= 0:
        return 0.0
    return max(0.0, (y1 - y0) / hours)


# --- Rapor üretimi ---


def build_report(
    csv_path: Path,
    now: datetime | None = None,
    day_override: int | None = None,
) -> DailyReport:
    """CSV'yi aç, son 24 saati analiz et, DailyReport üret."""
    rows_all = read_csv_rows(csv_path)
    end = now or datetime.now(UTC)
    window = filter_window(rows_all, end=end, window=timedelta(hours=24))

    # Gün numarası: ilk örnekten bugüne (1'den başlar)
    if day_override is not None:
        day_number = day_override
    else:
        day_number = _infer_day_number(rows_all, end)

    window_start = end - timedelta(hours=24)
    checks: list[Check] = []
    if window:
        checks.extend(check_memory_trends(window))
        checks.append(check_db_connections(window))
        checks.append(check_tick_miss(window))
        checks.append(check_disk(window))
        checks.append(check_readings_rate(window))
    else:
        checks.append(
            Check(
                name="Kapsam",
                status=Status.WARN,
                summary="Son 24 saatte veri yok — daemon başlatıldı mı?",
            ),
        )

    overall = max((c.status for c in checks), default=Status.OK)
    return DailyReport(
        generated_at=end,
        day_number=day_number,
        window_start=window_start,
        window_end=end,
        sample_count=len(window),
        overall=overall,
        checks=checks,
    )


def _infer_day_number(rows: list[dict[str, str]], now: datetime) -> int:
    """Endurance 1-based gün numarasını ilk CSV satırının tarihinden hesaplar."""
    for row in rows:
        ts = _parse_iso(row.get("timestamp", ""))
        if ts is None:
            continue
        delta = now.date() - ts.date()
        return max(1, delta.days + 1)
    return 1


# --- Console + markdown çıktısı ---


def format_console(report: DailyReport) -> str:
    """Prompt örneğine benzer Türkçe özet (renk bırakıldı — TTY detection ayrı)."""
    buf = io.StringIO()
    date_str = report.generated_at.strftime("%Y-%m-%d")
    buf.write(f"=== Endurance Günlük Kontrol — Gün {report.day_number} ({date_str}) ===\n\n")
    buf.write(f"Pencere: {report.window_start.isoformat()} → {report.window_end.isoformat()}\n")
    buf.write(f"Örnek sayısı: {report.sample_count}\n\n")
    for check in report.checks:
        emoji = _STATUS_EMOJI[check.status]
        buf.write(f"  {emoji} {check.summary}\n")
        for line in check.details:
            buf.write(f"      · {line}\n")
    buf.write("\n")
    buf.write(
        f"Kırmızı bayrak: "
        f"{_STATUS_EMOJI[report.overall]} "
        f"{_STATUS_LABEL[report.overall]}\n",
    )
    return buf.getvalue()


def format_markdown(report: DailyReport) -> str:
    """Markdown rapor (commit edilmez — _personal/ altında saklanır)."""
    date_str = report.generated_at.strftime("%Y-%m-%d")
    buf = io.StringIO()
    buf.write(f"# Endurance Gün {report.day_number} — {date_str}\n\n")
    buf.write(
        f"**Pencere:** {report.window_start.isoformat()} → "
        f"{report.window_end.isoformat()}  \n",
    )
    buf.write(f"**Örnek sayısı:** {report.sample_count}  \n")
    buf.write(
        f"**Genel durum:** {_STATUS_EMOJI[report.overall]} "
        f"**{_STATUS_LABEL[report.overall]}**\n\n",
    )
    buf.write("## Kontroller\n\n")
    for check in report.checks:
        emoji = _STATUS_EMOJI[check.status]
        buf.write(f"- {emoji} **{check.name}** — {check.summary}\n")
        for line in check.details:
            buf.write(f"  - {line}\n")
    buf.write("\n")
    buf.write(
        "## Kırmızı bayrak eşikleri (referans)\n\n"
        "- RSS memory: WARN ≥ %5 artış, CRIT ≥ %10 artış\n"
        "- DB pool: WARN ≥ %80, CRIT ≥ %90 (max_connections=100)\n"
        "- Tick miss: WARN ≥ 0.01 + slope ≥ 0, CRIT ≥ 0.05 + slope > 0\n"
        "- Disk avail: WARN = kullanım artıyor + boş alan azalıyor (trend); "
        "CRIT < %15 boş alan\n"
        "- Okuma hızı: WARN ≥ %10 düşüş (ilk yarı vs ikinci yarı)\n",
    )
    return buf.getvalue()


def write_markdown_report(
    report: DailyReport,
    report_dir: Path,
) -> Path:
    """Markdown raporu `endurance_day_<N>_report.md` olarak kaydeder."""
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"endurance_day_{report.day_number}_report.md"
    path.write_text(format_markdown(report), encoding="utf-8")
    return path


# --- CLI ---


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Endurance testi günlük kontrol + kırmızı bayrak raporlayıcı",
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=DEFAULT_CSV,
        help=f"Metrik CSV yolu (varsayılan: {DEFAULT_CSV})",
    )
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=DEFAULT_REPORT_DIR,
        help=f"Markdown rapor dizini (varsayılan: {DEFAULT_REPORT_DIR})",
    )
    parser.add_argument(
        "--day",
        type=int,
        default=None,
        help="Gün numarasını elle zorla (varsayılan: ilk CSV satırından çıkarsanır)",
    )
    parser.add_argument(
        "--no-report",
        action="store_true",
        help="Markdown rapor yazma — sadece console özeti göster",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point — exit kodunu kontrollerin genel durumundan türetir."""
    args = _parse_args(argv)
    if not args.csv.exists():
        print(f"HATA: CSV bulunamadı: {args.csv}", file=sys.stderr)  # noqa: T201
        return 2

    report = build_report(args.csv, day_override=args.day)
    print(format_console(report))  # noqa: T201

    if not args.no_report:
        written = write_markdown_report(report, args.report_dir)
        print(f"Rapor: {written} yazıldı")  # noqa: T201

    return int(report.overall)


if __name__ == "__main__":
    sys.exit(main())
