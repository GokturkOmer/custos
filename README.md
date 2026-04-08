# Custos

Otomasyon altyapısı olan endüstriyel tesislerde Modbus üzerinden sensör verisi okuyup, ML tabanlı anomali tespiti ve kullanıcı tanımlı eşik alarmları üreten, lokal çalışan bir edge izleme sistemi.

## Geliştirme

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pre-commit install
```

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

Aşama 1 — Proje İskeleti (devam ediyor)
