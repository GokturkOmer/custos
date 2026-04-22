# Custos

Otomasyon altyapısı olan endüstriyel tesislerde Modbus üzerinden sensör verisi okuyup, ML tabanlı anomali tespiti ve kullanıcı tanımlı eşik alarmları üreten, lokal çalışan bir edge izleme sistemi.

**Temel kural:** Sistem sadece okur, asla yazmaz. Prosese müdahale yoktur.

## Özellikler

- Modbus TCP Auto-Scan ile tag keşfetme ve aktivasyon
- Per-tag polling (Slow 10s / Normal 1s / Fast 100ms)
- Asset template sistemi (pompa, chiller, kompresör vb.) ile KPI hesabı
- ISA-18.2 uyumlu alarm state machine (debounce + hysteresis)
- Isolation Forest ile ML anomali tespiti
- Web Push bildirim (severity filtresi + sessiz saat)
- HTMX + Alpine.js + uPlot koyu tema dashboard
- Overview grafik tag seçimi (kullanıcı her grafiği özelleştirebilir)

## Hızlı Başlangıç (Geliştirme)

```bash
# 1. Sanal ortam
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pre-commit install

# 2. Veritabanı
cp .env.example .env        # Şifreyi değiştir!
docker compose up -d
alembic upgrade head

# 3. Sağlık kontrolü
python scripts/healthcheck.py   # OK görmeli
```

## Çalıştırma

### Dashboard (ana uygulama)

```bash
uvicorn custos.__main__:app --host 0.0.0.0 --port 8000
```

Tarayıcıda: `http://localhost:8000/dashboard`

ThresholdEngine, KpiEngine ve AnomalyDetector otomatik başlar.

### Critical loop (ayrı süreç)

```bash
python -m custos.critical
```

Aktif tag'leri DB'den okur, Modbus cihazlarından veri toplar, DB'ye yazar.
Yeni eklenen tag'leri ~60 tick'te otomatik alır (hot-reload).

### Modbus simülatörü (geliştirme için)

```bash
python -m custos.simulator
```

5 sanal sensör sunar (sıcaklık, basınç, debi, titreşim, RPM).

## Yapı

```
src/custos/
├── critical/              # Critical loop (Collector)
│   ├── collector.py       # Modbus okuyucu, per-tag polling
│   └── __main__.py        # Ayrı süreç entry point
├── analytics/             # Analytics loop
│   ├── threshold_engine.py  # Eşik alarmı (debounce + hysteresis)
│   ├── kpi_engine.py        # KPI hesaplama (AST-tabanlı formül)
│   ├── anomaly_detector.py  # Isolation Forest anomali tespiti
│   ├── push_sender.py       # Web Push bildirim
│   ├── scanner.py           # Modbus auto-scan
│   └── dashboard/           # FastAPI + Jinja2 web arayüzü
├── shared/                # Ortak kod
│   ├── database.py        # Abstract DB arayüzü + TimescaleDB impl
│   ├── config.py          # Pydantic Settings
│   ├── logging.py         # structlog yapılandırması
│   └── vapid.py           # VAPID key yardımcıları
└── simulator/             # Sahte Modbus TCP server
```

Diğer dizinler:
- `tests/` — 129 entegrasyon ve dashboard testi
- `deploy/` — systemd service, setup script, pilot dokümanı
- `scripts/` — VAPID key üretimi, model eğitimi, healthcheck
- `alembic/` — Veritabanı migration'ları (001-017)
- `docs/` — Proje brief'i

## Veritabanı

Custos lokal bir PostgreSQL/TimescaleDB instance'ına bağlanır.

```bash
docker compose up -d          # Başlat
docker compose down           # Durdur (veri korunur)
docker compose down -v        # Sıfırla (DİKKAT: tüm veriyi siler)
alembic upgrade head          # Migration çalıştır
```

## Test

```bash
ruff check .                  # Lint
mypy src/                     # Tip kontrolü
pytest tests/ -v              # Testler (DB ayakta olmalı)
```

## Pilot Deploy

Mini PC kurulumu için: `deploy/README_PILOT.md`

```bash
sudo bash deploy/setup.sh     # Otomatik kurulum
```

## Durum

F1-F8a + F11 A-H + F8b + W6 regresyon tamamlandı. Pilot kurulum: 5 Haziran 2026.
