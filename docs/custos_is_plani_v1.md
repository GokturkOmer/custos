# Custos — İş Planı v1

**Çıkış tarihi:** 20 Nisan 2026 (Pazartesi)
**Kritik milat:** AVM Pilot Go-Live = 5 Haziran 2026 (Cuma) — **46 gün**
**Statü:** Onaylandı (20 Nisan 2026)
**Kaynak belgeler:** [brief_v1.6.md](brief_v1.6.md), [custos_altyapi_vizyon_ozeti_v1.md](custos_altyapi_vizyon_ozeti_v1.md)

---

## BÖLÜM A — Pilot Öncesi Teknik Roadmap

Brief v1.6'nın W1–W7 haftalık dağılımı korunuyor; F11 (Historian) paketleri mevcut feature'larla paralel serpiştiriliyor. Dağıtık tercih — risk düşük, test yüzeyi geniş.

### Haftalık dağılım

| Hafta | Tarih | Ana feature | F11 paketleri (paralel) | F11 günlük yük |
|---|---|---|---|---|
| **W1** | 16–22 Nis | F8a Bakım (✅ tamamlandı) | — | — |
| **W2** | 23–29 Nis | F8b Chatbot (basit: semantic + chunking) | **A** (TimescaleDB migration) | 2–3 saat, W2 başı |
| **W3** | 30 Nis–6 May | F9 AVM Template Pack (✅ 22 Nis erken tamam — `fbe8ef3..21e8e9c`) | **B**+**C** (✅) | — |
| **W4** | 7–13 May | F10 AVM Deploy | **D** (dashboard auto-res) + **E** (Parquet arşiv) + **F** (retention UI) | ~2.5 gün (F10 gündüz, F11 akşam) |
| **W5** | 14–20 May | Saha entegrasyon 1 (binding) — arkadaşın saha keşfi sonrası | F11-I ✅ **22 Nis erken tamam** (`8285774..ab34edf`) | — |
| **W6** | 21–27 May | Saha entegrasyon 2 (tuning + binding) + kullanıcı kılavuzu | Denetim **A4** (subagent review) + **A6** (chaos) | ~3 gün denetim + 2 gün saha |
| **Feature freeze** | **27 May akşam** | Tüm feature kodu donar | — | — |
| **W7** | 28 May–3 Haz | Denetim **A5** (7 gün endurance pasif) + **A7** (deploy dry-run) + A4 fix + buffer | (ops.) coverage boost A4 bulgularına göre | ~7 gün A5 pasif + 1 gün A7 |
| **4 Haz** | — | A5 final rapor + **Go/No-Go** | — | — |
| **5 Haz** | — | **Pilot Go-Live** | — | — |

### Paketlerin iç detayı

**Paket A — TimescaleDB production hardening** _(W2 başı, 2–3 saat)_
- Yeni alembic migration: chunk interval = 1 gün, compression policy = 7 gün sonra, retention policy = 365 gün (ham)
- `compress_segmentby='tag_id'` — yanlışsa performans ters döner
- Mevcut veri üzerinde backfill compression test — pilot saatinde sürpriz olmasın
- Doğrulama: `SELECT hypertable_compression_stats('tag_readings');`

**Paket B — Continuous Aggregates** _(W3, 1 gün)_
- `tag_readings_1min` materialized view: AVG/MIN/MAX/STDDEV/COUNT per tag per dakika, refresh policy = her 5 dakika
- `tag_readings_1hour` aynı yapıda, refresh = her saat
- Test: geçmiş veriyi backfill, refresh policy çalışıyor mu doğrulama
- `COUNT` quality indicator olarak da kullanılır (o dakikada kaç okuma geldi?)

**Paket C — Auto-resolution query** _(W3, 3–4 saat)_
- `DatabaseInterface.query_readings_auto(tag_id, start, end)` — pencere büyüklüğüne göre katman seçer
- Karar mantığı: `(end-start)` ≤ 1h → ham; ≤ 1d → 1min agg; > 1d → 1hour agg
- Return tipi homogen (`TagReading` liste) — chart kodu değişmesin

**Paket D — Dashboard entegrasyon + paralelleştirme** _(W4, 4–5 saat)_
- Overview handler → tüm chart query'lerini `asyncio.gather(*)` ile toplu
- `query_tag_readings_downsampled` → `query_readings_auto` geçişi
- uPlot tarafına "resolution hint" badge (küçük: "saatlik agregat") — kullanıcı şaşırmasın

**Paket E — Parquet aylık arşiv job** _(W4, 1 gün)_
- `pyarrow` dependency eklenecek (pyproject.toml — kullanıcı onayı alındı)
- APScheduler job: her ayın 1'inde 02:00 TRT → bir önceki ay için 3 Parquet dosyası (ham/1min/1hour)
- Dizin yapısı: `/var/custos/archive/2026-05/tag_readings.parquet`
- Settings'te "şu an arşivle" manuel tetikleme butonu (test için)

**Paket F — Retention UI + disk telemetri** _(W4, 1 gün)_
- Settings sayfası: ham retention seçici (30/60/180/365/sınırsız), "auto-clean off" anahtarı
- Overview widget: disk doluluk çubuğu (yeşil <70%, sarı 70–85%, kırmızı >85%)
- Disk %85'te Web Push bildirimi

**Paket G — Collector paralelleştirme + enforcement** _(W5, 1 gün)_
- `critical/collector.py` for loop → per-host gather (bounded semaphore, N=5–10)
- Fast polling budget: warn değil **ValueError raise** — tag activation reddi + dashboard'da net mesaj
- Test: 100 tag × 1 Hz simülatörle yük testi, tick jitter ölçümü

**Paket H — Query guard** _(W6, 2–3 saat)_
- `query_readings_auto` içine: `(tag_count × time_range_days)` > eşik → aggregate'e düşür veya reddet
- Dashboard tarafında zoom aralığı limit'i — 3 yıldan geriye gidilmek istenirse "saat agg'a geçildi" uyarısı

**Paket I — Batch Modbus read** _(W5, 3–4 gün)_
- Register gruplama algoritması: aynı `(host, port, unit_id)` altında komşu/yakın `register_address`'leri (gap toleransı ~8 register) tek `read_holding_registers(address, count=N)` çağrısında birleştir
- Register type decode genişletme: uint16, int16, uint32, float32 desteği (şu an sadece uint16)
- Atomicity + fallback: batch hatasında per-tag retry — tek tag bozuksa tüm batch düşmesin
- **PLC yük koruması:** tag başına ayrı TCP round-trip yerine tek çağrı → saha cihazlarına ~10x daha az sorgu; "sadece okur, asla yazmaz" prensibinin okuma tarafı uzantısı
- Yük testi: 200 tag × gerçek profil (saha keşfi sonrası register haritasına göre)
- Bağımlılık: Saha keşfinden register komşuluk bilgisi (W4 öncesi lazım)

**F9 — AVM Template Pack (hibrit genişletilmiş kapsam)** _(W3, 6–7 gün)_

Kullanıcı 22 Nisan kararı ile C (hibrit) seçti ama kapsamı genişletti: tüm şablonlar şimdi + instance'lar saha sonrası.

- YAML loader + seed runner altyapısı (~1 gün) — `templates/` dizini ilk kez
- **9 şablon** (~5 gün):
  - Chiller
  - Enerji Analizörü
  - AHU (klima santrali)
  - FCU (fan coil)
  - Cooling Tower
  - Booster Pump Set
  - Sirkülasyon Pompası
  - Terfi Sistemi A (ör. atık su) — şablon + 4 instance toplam iki terfi türü
  - Terfi Sistemi B (ör. temiz su / yangın)
- Dashboard entegrasyonu + seed + test (~1 gün)

Not: 6–7 gün × 6 saat/gün = 36–42 saat. W3'e (5 iş günü × 6 saat = 30 saat) tam sığmaz, **W4'e kısa taşma** beklenir. F10 ile overlap olabilir; tolere edilir.

**Kullanıcı kılavuzu** _(W6, 1–2 gün)_
- Dashboard tur (sensors/alarms/kpis/overview/maintenance/assistant)
- Alarm yönetimi (durum geçişleri, kontrol listesi başlatma)
- Overview chart düzenleme (tag seçimi, zaman pencereleri, multi-axis)
- Bakım workflow'u (checklist, schedule, task tamamlama)
- Uzaktan destek prosedürü (VPN + temel teşhis adımları)
- PDF + kısa yüz yüze eğitim formatında teslim (pilot kabul madde 5)

### Kritik bağımlılıklar

1. **`pyarrow` ekleme onayı** — ✅ alındı (20 Nisan)
2. **Gerçek tag sayısı / polling mix + register adres haritası** — arkadaştan bekleniyor, W4 öncesi lazım (Paket I gruplama algoritması için register komşuluk bilgisi kritik). Saha keşfi check-list kişisel belge olarak hazırlandı (repo dışı) — Mayıs ortasına kadar ortağa iletilmeli.
3. **Mini PC (Intel N100/N200 + 2 TB NVMe)** — sahaya giderken alınacak, öncesi test yapılmayacak. Endurance/chaos/deploy dry-run tamamı ayrı WSL2 Ubuntu instance'ında yapılacak. DOA riski bilinçli kabul edildi.
4. **Kullanıcı kılavuzu PDF'i + VPN uzaktan destek testi** — W6 işi, pilot kabul madde 5 gereği.
3. **2 TB NVMe SSD siparişi** — pilot teslimine en geç W5 başı lazım

### Kesme önceliği (gecikme olursa feda sırası)

1. **Paket H** (query guard) — iç kullanım güvenliği, pilotta kritik değil → v1.1
2. **Paket F "disk doluluk uyarısı"** — Settings'te retention seçici kalabilir, push notification ertelenir
3. **Paket D'deki "resolution hint badge"** — sadece UX, kesilebilir
4. **Asla kesilmez:** A, B, C, E (Parquet), **I (batch read — PLC yük koruması)**. Pilot günü çalışıyor olmalı çünkü sonradan geriye dönük kurulum disk yapısını bozar. Paket I ayrıca saha cihazlarının güvenliği için kritik.

---

## BÖLÜM B — Pilot Sonrası Ticari Roadmap

Mantık: AVM pilotu başarıya ulaşırsa her sonraki adım açılır. Başarısızsa plan yeniden kurulur — burada happy path yazılı.

### Aylık takvim

| Ay | Ana hedef | Alt adımlar |
|---|---|---|
| **Haziran 2026** | Pilot izleme + stabilizasyon | Go-live hafta 1: günlük müşteri check-in. Hafta 2–4: haftalık. Hotfix bandı açık. Fabrika pilot hazırlık başlıyor (paralel, arka plan). |
| **Temmuz 2026** | Şirketleşme + fabrika pilot başlangıç | Limited şirket kurulumu (Temmuz başı — 7–10 iş günü sürecektir). Fabrika pilot kickoff (orta Temmuz). Logo, kurumsal e-posta, sözleşme şablonu finalize. |
| **Ağustos 2026** | Ücretsiz global kaynak başvuruları | Microsoft for Startups Founders Hub (~150K$ Azure credit), NVIDIA Inception (GPU/mentorluk), TimescaleDB Startup Program (cloud + mentorluk), AWS Activate. Her biri 30 dk–1 saat, hepsi aynı hafta. |
| **Eylül 2026** | Türkiye hibe başvuruları | TÜBİTAK BİGG (200K TL, bireysel → şirket konversiyonu uyumlu). KOSGEB Ar-Ge & İnovasyon (750K TL'ye kadar). AVM pilot referansı başvuruyu güçlendirir. **BİGG çağrı tarihini Ağustos'ta doğrula.** |
| **Ekim 2026** | Akseleratör / sektör programı | **Enerjisa EnerjiUp** (enerji verimliliği / AVM sektör — en iyi uyum). Alternatif: Arçelik Garage, Borusan Oltre. |
| **Kasım 2026** | Mentor outreach aktif faz | LinkedIn'de 2 mentor adayı seçili (OT + data/ML). Cold outreach mesajı, aylık 1 saat seans. Endeavor Türkiye kick-off değerlendirmesi. |
| **Aralık 2026** | v1.1 planlama + "Custos Benchmark" ilk sohbet | Pilot 6 aylık verisi değerlendir. v1.1 backlog'dan öncelik seç. İkinci ürün vizyonu için ayrı brief (v2.0 adayı). |

### İş paralel akışları

**Akış 1 — Hibe/destek başvuruları** _(Ağustos–Ekim)_
Her başvuru bağımsız; hepsi aynı anda ilerleyebilir. Ücretsiz olanlar (MS/NVIDIA/TS) risk yok, Ağustos'ta başvur. TÜBİTAK BİGG + KOSGEB paralel, tek dosya çoğu alanı ortak.

**Akış 2 — İkinci müşteri (fabrika pilotu)** _(Temmuz–Ağustos)_
AVM pilotundan öğrenilen dersler fabrika brief'ini şekillendirir. Go-live ~Eylül–Ekim tahmini.

**Akış 3 — v1.1 teknik backlog** _(Eylül–Aralık)_
Donmuş kalemler (cloud sync, çoklu-müşteri dashboard, BACnet/IP, SMS bildirim, LLM chatbot upgrade, **pyproject.toml bağımlılık beyaz liste denetimi**, **`DatabaseInterface.transaction()` context manager** — bulk import runtime atomik rollback + gelecek multi-insert akışları için temel) önceliklendirilir. Pilotlardan gelen gerçek talep baz alınır.

**Akış 4 — "Custos Benchmark" ikinci ürün** _(Kasım–Aralık)_
Kullanıcının mevcut paralel projesi ile kesişim konuşulacak. Başlangıç: ayrı brief taslağı, hedef pazar tanımı, teknik altyapı farkı (cross-tenant anonim aggregate katmanı).

### Karar eşikleri (tetik noktaları)

| Eşik | Gerçekleşirse | Aksiyon |
|---|---|---|
| AVM pilot kabul testi başarılı (5–15 Haz) | Müşteri yazılı memnuniyet | Şirketleşme Temmuz başı hızlanır, fabrika pilot teklifi tarih koyulur |
| AVM'de kritik bir alarm öngörü başarısı | Gerçek arıza erken yakalandı | Vaka çalışması yazılır, satış/başvurularda kanıt |
| İkinci müşteri (fabrika) talep imzaladı | Tekrarlanabilir iş modeli doğrulandı | Hibe başvurularına "gelir doğrulanmış" etiketi eklenir |
| Hibe alındı (BİGG / KOSGEB / akseleratör) | Nakit akışı | Üçüncü müşteri satışı için pazarlama bütçesi, ilk eleman ihtimali |
| İkinci ürün fikri olgunlaştı | Benchmark data pazarı netleşti | v2.0 brief yazımı, ayrı ekip/ortak arayışı |

### Takvim riskleri

1. **Şirketleşme süresi** — Türkiye'de limited kuruluş 7–10 iş günü. Temmuz başı başlamazsan Ağustos'a sarkar, hibe başvurularını erteler.
2. **TÜBİTAK BİGG çağrı dönemleri** — Yıllık 2 çağrı (genelde Şub/Eyl). Eylül çağrısı kaçırılırsa Şubat 2027'yi bekler. **Ağustos'ta çağrı tarihini doğrula.**
3. **AVM pilotunda beklenmedik saha sorunu** — Haziran buffer'ı sıkıştığı için destek geliştirme işini Temmuz'a itebilir; şirketleşme ve başvuruları geciktirir.

### Göktürk'ün özel gündemi (plan dışı)

- **Ortak ile gelir paylaşımı modeli netleştirme** — Göktürk kendisi yürütecek, iş planına bağlı kalem değil. Pilot başarısı sonrası doğal konuşma penceresi açılır.
- **Avukat ile veri mülkiyeti sözleşmesi** — Pilot öncesi (Mayıs) tamamlanması hedef. Kullanıcı yürütür.

---

## Kontrol listesi — 21–29 Nisan (önümüzdeki iki hafta)

### Bu hafta (W1 kalanı, 21–22 Nisan)
- [x] İş planı onayı (20 Nisan — tamamlandı)
- [x] `pyarrow` dependency ekleme onayı (alındı)
- [ ] 2 TB NVMe SSD siparişi

### Gelecek hafta (W2, 23–29 Nisan)
- [ ] Paket A başlangıç: W2 başı (23 Nisan Perşembe) — TimescaleDB migration taslak
- [ ] F8b Chatbot implementasyonu (basit scope: semantic + chunking)
- [ ] Arkadaşa tag/polling bilgisi hatırlatması (W4 öncesi lazım)
- [ ] Brief v1.7 taslağı yazmaya başla (düşük öncelik, F8b arkasında)

---

## Değişim ve yaşayan belge notu

Bu iş planı v1'dir. Değişiklik durumları:

- **Küçük revizyon** (takvim kayması, paket ince ayarı) → bu belge üzerinde doğrudan güncellenir, değişiklik notu en alta eklenir
- **Büyük revizyon** (pilot scope değişikliği, yeni müşteri, stratejik karar) → v2 olarak yeni dosya
- **Brief v1.7 yazıldığında** → bu belgedeki F11 paketleri brief'in §4.12'si ile senkron olacak

### Değişiklik notları

- **22 Nisan 2026:** F11 Paket I (Batch Modbus Read) W5 haftasına eklendi. Gerekçe: AVM pilotunda 200 tag öngörüldü; mevcut collector tag başına ayrı `read_holding_registers` çağrısı atıyor, PLC başına saniyede yüzlerce TCP round-trip Modbus slave'i (8–32 concurrent connection tipik) yorar. Komşu register'lar tek çağrıda okunursa ~10x daha az PLC yükü. "Sadece okur, asla yazmaz" prensibinin okuma tarafındaki uzantısı. Kesme önceliği: asla kesilmez (saha cihazları güvenliği).
- **22 Nisan 2026 (2):** F9 kapsam netleşti (C hibrit, genişletilmiş): 9 şablon + YAML altyapısı W3'te, 6–7 gün. Terfi 2 şablon × 4 instance. W6'ya kullanıcı kılavuzu eklendi. Saha keşfi check-list hazırlandı (kişisel belge, repo dışı — ortağa iletilecek). Mini PC sahaya giderken alınacak kararı kayıt altına alındı (test'ler ayrı WSL'de). Kapasite: 6 saat/gün × 44 gün = 264 saat, kalan işlerin tahmini 33–36 gün karşılığı — sıkı ama yeterli, buffer W7'de.
- **22 Nisan 2026 (3):** v1.1 backlog'una **pyproject.toml bağımlılık beyaz liste denetimi** eklendi (Bölüm B Akış 3). Gerekçe: denetim Adım 1 kod seviyesinde mimari kuralları kapsar ama pyproject.toml'a yasak paket eklenmesini yakalamaz. Pilot sonrası ekip büyümesiyle risk artar. Araç önerisi: `scripts/dependency_policy.py` + `[tool.custos.dependencies]` allowlist.
- **22 Nisan 2026 (4):** Denetim akışı revize edildi — **"önce fix/teknik borç → sonra test"** prensibi. A4/A5/A6/A7 W3'ten W6-W7'ye ötelendi (feature-complete sonrası). A5 endurance süresi 14 gün → **7 gün** (test ortamında 48-72 saatte görünen sorunlar yeterli güvence; pilotta zaten 14 gün gerçek yük koşacak 5-19 Haziran). Feature freeze 27 Mayıs akşam. Yeni akış: W3-W5 feature (F9+F10+Paket I+Saha1), W6 saha 2 + A4 + A6 + kılavuz, W7 A5 + A7 + fix + (ops.) coverage boost, 4 Haz Go/No-Go. Coverage boost A4 bulgularına göre opsiyonel; fail_under=65 regression guard zaten aktif. Detay: [docs/pilot_denetim_plani_v1.md](pilot_denetim_plani_v1.md) Genel takvim bölümü.
- **22 Nisan 2026 (5):** **F9 planlanandan 8 gün önce bitti** (30 Nis–6 May hedefi, gerçek 22 Nis). 6 commit `fbe8ef3..21e8e9c` origin/main'de, CI yeşil. 9 şablon + YAML loader + seed (idempotent upsert) + dashboard preview. 70 rol, 19 KPI, 29 advisory alarm, 28 advisory bakım. Test 339 → 366 (+27), coverage %66.50 → %67.04 (+0.54, fail_under=65 aşılmadı). Anahtar karar: advisory alarm/bakım DB'ye yazılmaz, instance binding W5 işi — şablon generic kalır. **Sonuç: W3 buffer'ı genişledi, F10 erken başlayabilir.** Ayrıca W5 scope adayı: "Önerilen eşikleri uygula" butonu (advisory'yi binding wizard'a entegre — şu an operatör manuel aktarmak zorunda).
- **22 Nisan 2026 (6):** **F10 AVM Deploy W4 planından erken bitti** (planlı 7-13 May, gerçek 22 Nis, **~14 gün erken**). 4 commit `6c4b362..6066cb8` origin/main'de, CI yeşil (3m24s). Paket A: setup.sh finalize (138→269 satır, TimescaleDB zorunlu, openssl DB şifre idempotent, seed_asset_templates otomatik, 10 adım, exit code 0/1/2/3/4). Paket B: **2-service modeli** (benim önerim 3 idi, mevcut mimari test edilmiş → Claude Code doğru yönlendirdi) — custos.service + custos-critical.service, archive dashboard lifespan'ında entegre. Paket C: healthcheck 6 kontrol + `--json` + VAPID `--write-env` idempotent + ECPublicKey bug fix. Paket D+E: README_PILOT.md 126→326 satır 15 bölüm brief v1.7 spec (16 GB RAM, 2 TB NVMe), `_personal/pilot/deploy_dry_run_checklist.md`. 105 unit test + smoke. **W3+W4 buffer'ı tamamen açıldı** (~3 hafta ekstra zaman).
- **22 Nisan 2026 (7):** **F11 Paket I (Batch Modbus Read) W5 planından erken bitti** (planlı 14-20 May, gerçek 22 Nis, **~22 gün erken**). 5 commit `8285774..ab34edf` origin/main'de, CI yeşil (3m34s). 49 yeni test (batch_grouper 15 + register_decoder 30 + integration 4). Coverage %67.04 → %67.93 (+0.89, fail_under=65 aşıldı). batch_grouper %99, register_decoder %100, collector %89. Özellikler: register gruplama (gap_tolerance=8, max 125 register/batch), 5 register tipi (uint16/int16/uint32/int32/float32 × big/little word order), atomicity fallback (batch error → per-tag retry), 200 tag yük testi tick miss < %5, config flags (`collector_batch_read_enabled`, `collector_batch_gap_tolerance`). `byte_order` TagRecord alanı mevcut olduğu için migration gerekmedi. Simülatöre yeni register tipi eklenmedi (scope kontrolü — mixed types pymodbus mock ile kapsandı).
- **22 Nisan 2026 (8):** **Bulk Import Helper tamamlandı** (planlı ~1 gün, gerçek ~7 saat, aynı gün). 3 atomik commit `7492494..72f12f3` origin/main'de, CI yeşil (3m33s). Backend (`bulk_import.py` — BulkImportRow pydantic whitelist validasyon), 4 route (modal/preview/commit/example indirme — HX-Request'e göre HTML partial ya da JSON), Alpine+HTMX modal UI, 3 örnek dosya (`docs/examples/tag_import_example.csv` + `.yaml` + README). 43 yeni test (26 unit + 8 integration + 9 dashboard), önceki 415 → 458 total pass. **Performans: 200 tag 0.36s parse+validate+DB insert (~20.000x manuel girişe göre).** Saha günü tahmini kazanç 6-7 saat. Duplicate davranışı tüm 3 mod (reject/update/insert) + aynı dosyada duplicate + invalid satır rollback doğrulandı. **Atomik rollback sınırlı:** validation aşaması atomik (tek hata → hiçbiri yazılmaz), runtime DB hatası **kısmi yazıma yol açabilir** çünkü `shared/database.py` transaction context manager expose etmiyor. v1.1 backlog'a eklendi (Bölüm B Akış 3). **TÜM v1.0 KODU BİTTİ — FEATURE-COMPLETE.** Pilot öncesi **~6 hafta erken** tamamlandı (22 Nis → 5 Haz pilot). Sıradaki: ayrı WSL endurance denemesi (kullanıcı erken deneme planı — 7 gün pasif + günlük 15 dk operatör simülasyonu) + kullanıcı kılavuzu + A4 subagent review + saha entegrasyonu (arkadaş dönüşü sonrası).
