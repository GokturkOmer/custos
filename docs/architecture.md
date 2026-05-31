# Custos — Mimari

Bu belge Custos'un mimarisini bütüncül olarak anlatır. Tek tek kararların
**neden** öyle verildiği için [Mimari Karar Kayıtları (ADR)](adr/README.md)'na
bakın. Değişmez kurallar [`CLAUDE.md`](../CLAUDE.md)'de, kuralların otomatik
denetimi [`scripts/architecture_check.py`](../scripts/architecture_check.py)'dedir.

## 1. Custos nedir?

Custos, ticari veya endüstriyel tesislerde **Modbus TCP** üzerinden sensör verisi
okuyan, ML tabanlı anomali tespiti ve kullanıcı tanımlı eşik alarmları üreten,
verileri yıllarca lokal saklayan bir **edge izleme sistemidir**. Üç katman olarak
konumlanır:

1. **Anlık izleme** — Modbus okuma, alarm, anomali, bakım, asistan.
2. **Lokal historian** — Yüksek çözünürlüklü verinin uzun vadeli (365 gün ham +
   3 yıl dakika + sınırsız saat) lokal saklanması.
3. **AR-GE veri temeli** — Saha verisinin gelecekte ESG/predictive-maintenance/
   benchmark için kullanılacak bir varlığa dönüşmesi (vizyon).

**İki temel ilke:**
- **Sadece okur, asla yazmaz.** Prosese müdahale yoktur ([ADR-002](adr/002-read-only-modbus.md)).
- **Veri lokal kalır, dışarı çıkmaz.** Cloud/S3/NAS senkronizasyonu yok; uzak
  destek yalnızca müşteri onaylı VPN ile, dosya aktarımı olmadan.

## 2. Tasarım ilkeleri

| İlke | Karar |
|---|---|
| Kritik izleme, analitikten yalıtılmalı | İki bağımsız süreç ([ADR-001](adr/001-two-process-architecture.md)) |
| İzleme aracı prosese zarar veremez | Sadece-okuma Modbus, CI ile zorlanır ([ADR-002](adr/002-read-only-modbus.md)) |
| Veri uzun saklanmalı, sorgu hep hızlı olmalı | Katmanlı TimescaleDB historian ([ADR-003](adr/003-timescaledb-historian.md)) |
| ML edge'e uygun ve açıklanabilir olmalı | sklearn / Isolation Forest, derin öğrenme yok ([ADR-004](adr/004-isolation-forest-not-deep-learning.md)) |
| Arayüz tek kişiyle sürdürülebilir olmalı | Sunucu-merkezli HTMX, SPA yok ([ADR-005](adr/005-htmx-not-spa.md)) |
| Alarm gürültüsü engellenmeli | ISA-18.2 prensipleri ([ADR-006](adr/006-isa-18.2-alarm-management.md)) |

## 3. İki süreçli mimari ve veri akışı

Custos iki bağımsız işletim sistemi süreci olarak çalışır. Aralarındaki tek
iletişim, paylaşılan TimescaleDB'dir — doğrudan import yoktur.

```
        Modbus TCP cihazları (PLC, sensör, enerji analizörü)
                     │  sadece okuma (FC01–FC04)
                     ▼
   ┌─────────────────────────────────┐
   │  KRİTİK DÖNGÜ                    │   python -m custos.critical
   │  Collector → Register Decoder    │   (custos-critical.service)
   │  → Threshold Engine → Alarm      │   ML YOK · min. bağımlılık
   └────────────────┬────────────────┘
                    │ yaz
                    ▼
            ┌────────────────────┐
            │   TimescaleDB      │   tag_readings (ham)
            │   (historian)      │   tag_readings_1min / _1hour
            │                    │   features · alarmlar · bakım …
            └─────────┬──────────┘
                    │ oku
                    ▼
   ┌─────────────────────────────────┐
   │  ANALİTİK DÖNGÜ + DASHBOARD     │   uvicorn custos.__main__:app
   │  Anomaly · KPI · SPC · Liveness  │   (custos.service)
   │  Maintenance · Archive · Disk    │
   │  FastAPI + HTMX dashboard ───────┼──► Operatör (lokal ağ, TLS/Caddy)
   │  Web Push ───────────────────────┼──► Tarayıcı / mobil bildirim
   └────────────────┬────────────────┘
                    ▼
        Parquet aylık arşiv  (/var/custos/archive/YYYY-MM/)
```

- **Kritik Döngü** deterministiktir, tick miss hedefi ~0; ML/numerik kütüphane
  import etmez.
- **Analitik Döngü** "best effort"tur; ağır işleri yapar, dashboard'u sunar.
- Yeni eklenen tag'ler Kritik Döngü tarafından DB'den ~60 tick'te otomatik alınır
  (hot-reload); süreçler birbirini yeniden başlatmaz.
- **Alarm üretimi Kritik Döngü'dedir** (review H1, 2026-05-31): kullanıcı-tanımlı
  eşik alarmları `critical/threshold_watcher.py` ile Collector'ın yanında ayrı bir
  task olarak üretilir — Collector'ın yayımladığı in-memory son değerleri okur (DB
  round-trip'siz), breach + debounce/hysteresis değerlendirir, `alarm_events`'e
  yazar. Critical **push göndermez**; pywebpush + VAPID Analitik'te kalır,
  Analitik'teki push-dispatch loop'u `pushed_at IS NULL` eşik alarm'larını iletir.
  Böylece alarm üretimi Analitik'in çökmesinden/ML yükünden gerçekten izole olur.
  Layer-1 kuralları (rate-of-change + cross-sensor) Analitik'te kalır.

## 4. Modül haritası

```
src/custos/
├── critical/                 # KRİTİK DÖNGÜ — deterministik, ML yok
│   ├── collector.py          # Modbus okuma, per-tag polling, paralelleştirme
│   ├── register_decoder.py   # uint16/uint32 → fiziksel değer (gain/offset/swap)
│   ├── batch_grouper.py      # Komşu register'ları batch okuma için gruplama
│   ├── threshold_watcher.py  # Eşik breach + debounce/hysteresis → alarm (push YOK)
│   └── __main__.py           # `python -m custos.critical` giriş noktası
│
├── analytics/                # ANALİTİK DÖNGÜ + DASHBOARD
│   ├── threshold_engine.py   # Layer-1: rate-of-change + cross-sensor (eşik artık critical'da)
│   ├── anomaly_detector.py   # Isolation Forest + mode-aware residual
│   ├── spc_engine.py         # İstatistiksel proses kontrolü (EWMA/kontrol kartı)
│   ├── liveness_engine.py    # Tag canlılık / stuck-at tespiti
│   ├── kpi_engine.py         # AST-tabanlı KPI formül motoru
│   ├── escalation.py         # Alarm yükseltme (warn→crit)
│   ├── heartbeat.py          # Servis kalp atışı
│   ├── scanner.py            # Modbus auto-scan
│   ├── push_sender.py        # Web Push bildirim (to_thread + timeout)
│   ├── push_dispatch.py      # Critical'ın yazdığı eşik alarm'larını push eder (pushed_at)
│   ├── maintenance_mode.py   # Alarm shelving (bakım modu)
│   ├── maintenance_scheduler.py  # Periyodik bakım takvimi
│   ├── archiver.py           # Parquet aylık arşivleyici
│   ├── archive_scheduler.py  # Arşiv zamanlayıcı (asyncio tick)
│   ├── disk_telemetry.py     # Disk doluluk telemetri + push uyarı
│   ├── resource_telemetry.py # CPU/RAM telemetri
│   ├── assistant/            # Teknik asistan (sentence-transformers + FAISS)
│   ├── dashboard/            # FastAPI app + route'lar + Jinja2 + statik
│   └── templates/            # Asset template (YAML) yükleyici
│
├── shared/                   # ORTAK KATMAN (her iki süreç de kullanır)
│   ├── database.py           # Soyut DatabaseInterface + TimescaleDB impl (tek DB noktası)
│   ├── config.py             # Pydantic Settings (.env'den okur)
│   ├── logging.py            # structlog yapılandırması
│   ├── auth.py               # Oturum / parola (bcrypt)
│   ├── query_guard.py        # Aşırı sorgu koruması
│   ├── watchdog.py           # systemd watchdog entegrasyonu
│   ├── vapid.py              # Web Push VAPID anahtarları
│   ├── threshold_core.py     # Eşik karar çekirdeği (breach/hysteresis/debounce)
│   ├── maintenance.py        # Bakım modu gerçek-zamanlı kontrolleri (paylaşılan)
│   ├── maintenance_periods.py
│   └── stuck_at_presets.py
│
└── simulator/                # Sahte Modbus TCP server (yalnızca geliştirme/test)
    ├── modbus_server.py
    ├── patterns.py
    └── sensors.py
```

Asset şablonları **veri olarak** tanımlanır: `templates/*.yaml` (chiller, AHU,
FCU, cooling tower, pompalar, enerji analizörü…); kod yalnızca bu YAML'leri
yükler.

## 5. Veri katmanları (historian)

Ayrıntı için [ADR-003](adr/003-timescaledb-historian.md).

| Katman | Yapı | Saklama | Tazeleme |
|---|---|---|---|
| `tag_readings` | Hypertable, chunk 1 gün, compress 7 gün | 365 gün | — |
| `tag_readings_1min` | Continuous aggregate | 3 yıl | 5 dk |
| `tag_readings_1hour` | Hierarchical CA | Sınırsız | 30 dk |
| Parquet arşiv | Aylık columnar (LZ4) | Sınırsız | Aylık |

- **Auto-resolution query** (`query_readings_auto`): pencereye göre doğru
  katmandan okur (≤1s ham, ≤1g dakika, >1g saat) — sorgu performansı her zoom'da
  sabit kalır.
- **Query guard**: `(tag_count × gün)` eşiğini aşan sorguyu üst agregata zorlar
  veya reddeder.
- Saklama varsayılanları **cömerttir**; kısıtlama müşterinin Settings'ten verdiği
  bilinçli karardır.

## 6. Sadece-okuma güvenlik modeli

Custos OT güvenliğini mimarinin merkezine koyar:

- Modbus istemci yalnızca okuma fonksiyon kodlarını (FC01–FC04) kullanır; yazma
  fonksiyonları kod tabanında yoktur.
- Bu, [`architecture_check.py`](../scripts/architecture_check.py) `MODBUS_WRITE`
  kuralıyla CI + pre-commit'te zorlanır — yazma kodu repoya giremez.
- Uzak erişim yalnızca müşteri onaylı VPN; veri dışarı aktarılmaz.
- Dashboard TLS arkasında (Caddy reverse proxy), oturum tabanlı kimlik
  doğrulama + IP/kullanıcı bazlı rate limit + güvenlik başlıkları (CSP vb.).

## 7. CI ile zorlanan mimari sözleşme

Custos'un en ayırt edici yanı, mimari kuralların yorum/disiplinle değil
**makineyle** korunmasıdır. [`scripts/architecture_check.py`](../scripts/architecture_check.py)
her commit'te (pre-commit) ve CI'da çalışır; 11 kuralı statik analizle denetler:

| Kural | Ne korur |
|---|---|
| `ML_IN_CRITICAL` | Kritik Döngü'de ML/numerik kütüphane import edilemez |
| `ANALYTICS_IMPORTS_CRITICAL` | Analitik kod `custos.critical`'ı import edemez (süreç bağımsızlığı) |
| `MODBUS_WRITE` | Modbus yazma fonksiyonu çağrısı yasak (sadece-okuma) |
| `SQL_OUTSIDE_DATABASE` | SQL yalnızca `shared/database.py`'de yazılır |
| `SQL_IN_COLLECTOR` | Collector SQL string yazamaz |
| `ASYNCPG_IN_COLLECTOR` | Collector asyncpg'yi doğrudan kullanamaz |
| `DB_DRIVER_NON_ASYNCPG` | asyncpg dışı DB driver/ORM yasak |
| `DEEP_LEARNING` | torch/tensorflow/keras/jax doğrudan import yasak |
| `DATETIME_NOW_NAIVE` | `datetime.now()` parametresiz yasak (UTC zorunlu) |
| `DATETIME_UTCNOW` | `datetime.utcnow()` yasak (deprecated + naive) |
| `PRINT_STATEMENT` | `print()` yasak; structlog kullanılır |

Yanlış pozitifler için `# allow-arch-check: <sebep>` istisna mekanizması vardır
(sebep zorunlu, grep edilebilir). Bu sözleşme, [ADR-001](adr/001-two-process-architecture.md)
ve [ADR-002](adr/002-read-only-modbus.md)'nin zaman içinde bozulmasını engeller.

## 8. Teknoloji yığını

| Katman | Teknoloji |
|---|---|
| OS | Ubuntu 24.04 LTS |
| Backend | Python 3.11+, FastAPI, Uvicorn |
| Veritabanı | PostgreSQL 16 + TimescaleDB 2.25 (asyncpg) |
| Modbus | pymodbus (<3.13.0 pinli) |
| Frontend | Jinja2 + HTMX 2.0 + Alpine.js 3.14 + uPlot 1.6 |
| CSS | Tailwind 3.4 (standalone binary, build adımı yok) |
| ML | scikit-learn, numpy (derin öğrenme yok) |
| Asistan | sentence-transformers + faiss-cpu |
| Arşiv | pyarrow (Parquet) |
| Bildirim | pywebpush |
| Migration | Alembic (`alembic/versions/`) |
| Dağıtım | Docker Compose + systemd + Caddy (TLS) |

## 9. Dağıtım

- İki systemd servisi: `custos-critical.service` (Kritik Döngü) ve
  `custos.service` (Analitik + Dashboard). İkincisi DB'ye, birincisi ikinciye
  bağımlıdır.
- `deploy/setup.sh` idempotent kurulum yapar: TimescaleDB repo + paket, rastgele
  DB şifresi + `.env`, VAPID anahtar üretimi, dizin yapısı, UFW kuralları.
- Veritabanı şeması Alembic migration'larıyla yönetilir (`alembic upgrade head`).
- Saha kurulum kılavuzu: [`deploy/README_PILOT.md`](../deploy/README_PILOT.md).

## 10. İlgili belgeler

- [Mimari Karar Kayıtları (ADR)](adr/README.md) — kararların gerekçeleri
- [`CLAUDE.md`](../CLAUDE.md) — değişmez geliştirme kuralları
- [`docs/brief_v1.7.md`](brief_v1.7.md) — ürün brief'i (kanonik kapsam)
- [`docs/custos_operator_kilavuzu_v1.md`](custos_operator_kilavuzu_v1.md) — operatör kılavuzu
- [`scripts/architecture_check.py`](../scripts/architecture_check.py) — mimari kural denetleyici
