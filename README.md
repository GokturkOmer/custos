# Custos

Otomasyon altyapısı olan endüstriyel tesislerde Modbus üzerinden sensör verisi okuyup, ML tabanlı anomali tespiti ve kullanıcı tanımlı eşik alarmları üreten, lokal çalışan bir edge izleme sistemi.

## Geliştirme

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pre-commit install
```

## Veritabanı

Custos lokal bir TimescaleDB instance'ına bağlanır. Geliştirme için Docker üzerinden çalıştırılır.

### İlk kurulum

1. `.env.example`'ı kopyala: `cp .env.example .env`
2. `.env` içindeki şifreyi değiştir
3. Veritabanını başlat: `docker compose up -d`
4. Migration'ları çalıştır: `python -m alembic upgrade head`
5. Sağlık kontrolü: `python scripts/healthcheck.py` → `OK` görmelisin

### Veritabanını durdurma

`docker compose down` (veri kaybolmaz, volume'da kalır)

### Veritabanını sıfırlama (DİKKAT — tüm veriyi siler)

`docker compose down -v && docker compose up -d && python -m alembic upgrade head`

## Yapı

```
src/custos/
├── critical/    # Critical loop süreci (Collector + Threshold Engine)
├── analytics/   # Analytics loop süreci (Feature Engine + ML + Dashboard)
└── shared/      # İki süreç arası ortak kod (config, logging, database)
```

- `tests/` — Birim ve entegrasyon testleri
- `docs/` — Proje dokümanları
- `scripts/` — Yardımcı scriptler

## Durum

Aşama 2 — Veri katmanı (devam ediyor)
