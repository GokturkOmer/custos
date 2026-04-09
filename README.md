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

## Walking Skeleton'ı Çalıştırma

Aşama 3'ten itibaren uçtan uca veri akışı manuel olarak test edilebilir.

### 3 terminalli yöntem

Terminal 1 — Veritabanı:
```
docker compose up -d
```

Terminal 2 — Modbus simülatörü:
```
.venv\Scripts\activate
python -m custos.simulator
```

Terminal 3 — Collector:
```
.venv\Scripts\activate
python -m custos.critical
```

10-30 saniye çalıştır, sonra her ikisini de Ctrl+C ile durdur.

### Veriyi görüntüleme

```
python scripts/query_last_readings.py
```

5 sensör için son 60 saniyedeki okumaların özet tablosunu gösterir.

## Durum

Aşama 3 — Walking skeleton (tamamlandı)
