# ADR-001: İki süreçli mimari (Kritik Döngü + Analitik Döngü)

**Tarih:** 2026-05-22
**Durum:** Kabul (genişletildi → üç süreç, bkz. [ADR-008](008-assistant-separate-service.md))

> **Güncelleme (2026-05-29):** Bu ADR'nin kurduğu Kritik + Analitik iki-süreç
> sınırı **aynen geçerlidir**. Asistan modülünün (PDF görsel retrieval)
> genişlemesiyle sisteme **üçüncü** bir bağımsız süreç eklendi: Asistan
> (`python -m custos.assistant`, port 8001, kendi systemd unit'i + kaynak
> limiti). Gerekçe ve detaylar: [ADR-008](008-assistant-separate-service.md).
> Aşağıdaki iki-süreç anlatısı bu üçüncü süreci kapsayacak şekilde okunmalıdır:
> üç süreç de doğrudan import etmez, yalnızca paylaşılan PostgreSQL üzerinden
> haberleşir; `architecture_check.py` artık asistan sınır kurallarını da içerir
> (toplam 14 kural).
>
> **Güncelleme (2026-05-31, review H1):** Bu ADR baştan beri Threshold + Alarm'ı
> Kritik Döngü'ye koyar; ancak kod zamanla kaymış ve `ThresholdEngine` Analitik
> sürecinde (Isolation Forest / dashboard ile aynı event loop) çalışır olmuştu —
> ADR'nin çekirdek vaadi ("analitik çökse bile alarm üretimi devam eder") fiilen
> karşılanmıyordu. Bu kayma düzeltildi: kullanıcı-tanımlı eşik alarm üretimi
> `critical/threshold_watcher.py`'a taşındı (Collector'ın yayımladığı in-memory
> son değerleri okuyan ayrı task; **push göndermez** — Analitik'teki push-dispatch
> loop'u `pushed_at IS NULL` eşik alarm'larını iletir). `ThresholdEngine` artık
> yalnız Layer-1 (rate-of-change + cross-sensor) değerlendirir. Eşik karar mantığı
> `shared/threshold_core.py`'da tek kaynaktır; bakım gerçek-zamanlı kontrolleri
> `shared/maintenance.py`'ye taşındı (Critical Analitik'i import etmez). Uçtan uca
> walking-skeleton ile doğrulandı.

## Bağlam

Karar proje başlangıcından (brief v1.0) beri geçerlidir; bu ADR onu yazıya geçirir.

Custos tek bir edge cihazda (fansız mini PC) iki çok farklı iş yükünü aynı anda
yürütmek zorunda:

1. **Deterministik gerçek-zamanlı izleme** — Modbus tag'lerini sabit aralıklarla
   yoklamak, eşik kontrolü yapmak ve alarm tetiklemek. Bu yolun gecikme
   bütçesi dardır ve tick kaçırma (tick miss) oranı sıfıra yakın olmalıdır.
2. **Ağır analitik** — ML tabanlı anomali tespiti (Isolation Forest), KPI
   hesaplama, feature engineering, dashboard sunumu, Parquet arşivleme. Bu işler
   CPU/bellek dalgalanması yaratır ve doğası gereği "best effort"tur.

Bu iki yük tek bir süreçte çalışırsa, analitik tarafındaki bir CPU yükü, bellek
tahsisi veya bir ML kütüphanesindeki çökme **doğrudan kritik izleme yolunu**
düşürür veya geciktirir. Endüstriyel izlemede izlemenin durması kabul edilemez.

## Karar

Sistem **iki bağımsız işletim sistemi sürecine** ayrılır:

```
┌─────────────────────────────┐     ┌──────────────────────────────────────┐
│  KRİTİK DÖNGÜ                │     │  ANALİTİK DÖNGÜ + DASHBOARD            │
│  python -m custos.critical  │     │  uvicorn custos.__main__:app          │
│                             │     │                                        │
│  • Collector (Modbus oku)   │     │  • Anomaly / KPI / SPC / Liveness      │
│  • Threshold Engine         │     │  • Maintenance scheduler               │
│  • Alarm dispatch           │     │  • Parquet arşiv + Disk monitor        │
│  • ML YOK, min. bağımlılık  │     │  • FastAPI dashboard + Web Push        │
│  custos-critical.service    │     │  custos.service                        │
└──────────────┬──────────────┘     └─────────────────┬──────────────────────┘
               │                                       │
               │       paylaşılan TimescaleDB          │
               └───────────────┬───────────────────────┘
                               ▼
                    ┌────────────────────────┐
                    │  custos.shared          │
                    │  config · logging       │
                    │  DatabaseInterface      │
                    │  domain models          │
                    │  historian helpers      │
                    └────────────────────────┘
```

- **Kritik Döngü** (`custos.critical`): Collector + Threshold Engine + alarm
  tetikleme. ML/numerik kütüphane import etmez; bağımlılığı `pymodbus` + soyut
  DB arayüzü ile sınırlıdır.
- **Analitik Döngü + Dashboard** (`custos.__main__`): Anomali, KPI, SPC,
  liveness, bakım zamanlayıcı, Parquet arşivleyici, disk telemetri ve FastAPI
  dashboard'u; analitik motorlar uygulama lifespan'ında başlar.
- **Ortak katman** (`custos.shared`): Konfigürasyon, loglama, soyut veritabanı
  arayüzü (`DatabaseInterface`), domain modelleri, historian yardımcıları.
- **Süreçler arası iletişim yalnızca paylaşılan TimescaleDB üzerindendir.**
  Doğrudan import yoktur. Kritik Döngü yeni eklenen tag'leri DB'den ~60 tick'te
  otomatik alır (hot-reload).

Bu sınır **makine ile zorlanır** ([`architecture_check.py`](../../scripts/architecture_check.py),
CI + pre-commit):

- `ML_IN_CRITICAL` — Kritik Döngü'de sklearn/numpy/torch vb. import yasak.
- `ANALYTICS_IMPORTS_CRITICAL` — Analitik kod `custos.critical`'ı import edemez.

## Sonuçlar

**Pozitif:**
- Kritik izleme yolu analitik yükünden tam izole; tick miss hedefi ~0 korunur.
- Bir sürecin çökmesi diğerini düşürmez (anomali motoru patlasa bile alarm üretimi
  devam eder, ve tersi).
- Bağımlılık ayrışması: Kritik Döngü'ye yıllarca ağır kütüphane sızmaz; saldırı
  yüzeyi ve yeniden başlatma süresi küçük kalır.
- Mimari sınır okunabilir ve denetlenebilir; sözleşme CI'da otomatik korunur.

**Negatif:**
- İki systemd unit (`custos-critical.service` + `custos.service`) ve bunların
  koordinasyonu (servis bağımlılıkları) gerekir.
- Süreçler "anlık" mesajlaşmaz; koordinasyon DB üzerinden olur (yeni tag ~60
  tick gecikmeyle alınır). Bu, gerçek-zamanlı kontrol değil izleme sistemi
  olduğumuz için kabul edilebilir.
- Paylaşılan TimescaleDB tek ortak nokta hâline gelir (her iki süreç de ona bağlı).

## Alternatifler

- **Tek süreç, asyncio:** ML çıkarımı event loop'u bloklar, tick kaçar. Reddedildi.
- **Tek süreç, thread havuzu:** Python GIL nedeniyle CPU-yoğun ML işleri yine
  kritik thread'i aç bırakır; ayrıca paylaşılan durum hata ayıklamayı zorlaştırır.
  Reddedildi.
- **Mikroservis + mesaj kuyruğu (ör. Redis/RabbitMQ):** Tek bir edge cihaz için
  aşırı; ek hareketli parça, ek bağımlılık, ek hata modu. CLAUDE.md "minimum
  hareketli parça" kuralına aykırı. Reddedildi.
