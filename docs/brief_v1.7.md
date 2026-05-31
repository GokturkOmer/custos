# Custos — Proje Brief

**Versiyon:** 1.7
**Tarih:** 21 Nisan 2026
**Durum:** Asıl pilot AVM (ücretli, 5 Haziran 2026). Fabrika pilotu Temmuz 2026'ya ertelendi. F11 Historian & Retention Stack tamamlandı (21 Nisan 2026).
**Önceki versiyonlar:** v1.0 (8 Nisan), v1.1, v1.2 (Custos adı), v1.3 (10 Nisan, pilot müşteri), v1.4 (11 Nisan, tag akışı netleştirme), v1.5 (14 Nisan, bakım modülü + chatbot), v1.6 (16 Nisan, AVM pilotu)

---

## 0. Bu versiyonda ne değişti

v1.6 → v1.7 değişiklikleri:

- **Lokal historian rolü ürün tanımına girdi** — Custos artık üç katmanlı bir ürün olarak konumlanır: anlık izleme + lokal historian + AR-GE veri temeli. "Verileriniz dışarı çıkmaz, sizde kalır, ileride sizin için değer üretecek varlığa dönüşür" satış dili eklendi.
- **F11: Historian & Retention Stack tamamlandı** — 8 paket (A-H), 7 commit, 21 Nisan'da bitti. Brief'e §4.12 olarak yazıldı (kanonik kaynak: `docs/custos_altyapi_vizyon_ozeti_v1.md` + `docs/custos_is_plani_v1.md`).
- **Veri katmanları yapısı kararlaştı** — Ham 365 gün + dakika agregat 3 yıl + saat agregat sınırsız + Parquet aylık arşiv (sınırsız, müşteri isterse siler). Cömert default; müşteri isterse Settings'ten kısaltır.
- **Donanım revizyonu: pilot başına 2 TB NVMe SSD** (v1.6'daki 500 GB'dan yükseltme). Ek maliyet ~80–150 €, teklife açık kalem.
- **Auto-resolution query API** — Dashboard chart sorguları zoom seviyesine göre doğru katmandan okur (≤1 saat = ham, ≤1 gün = dakika, >1 gün = saat). Performans her koşulda sabit.
- **Parquet arşiv** — Aylık snapshot, `/var/custos/archive/YYYY-MM/` müşterinin erişebildiği klasörde. TimescaleDB'den bağımsız okunabilir format (Apache Arrow). 20 yıl sonra da yaşar.
- **Retention UI** — Settings sayfasında ham veri saklama seçici (30/60/180/365 gün veya sınırsız), "auto-clean off" anahtarı, disk doluluk widget'ı (yeşil <%70, sarı %70-85, kırmızı ≥%85), %85'te Web Push uyarısı.
- **Collector paralelleştirildi** — Per-host `asyncio.gather` + bounded semaphore (varsayılan 5). Fast Polling Budget artık warn değil **ValueError raise** — tag aktivasyon noktasında reddediliyor; UI'da net mesaj.
- **Query guard** — `query_readings_auto`'da `(tag_count × time_range_days) > eşik` kontrolü; aşılırsa bir üst katmana zorla veya reddet (HTTP 400).
- **Uzak erişim modeli netleşti** — Veri sync YOK (cloud, S3, NAS hiçbirine veri gönderimi yapılmaz). Müşteri onaylı VPN sadece bakım/destek erişimi için. "Veriniz dışarı çıkmıyor" argümanı bozulmuyor.
- **Yeni çalışma kuralı (Kural 13):** *Veri saklama varsayılanları her zaman cömert; kısıtlama müşterinin bilinçli kararıdır.*
- **Yeni risk:** Disk büyüme hızı — retention UX'i yeterince net anlatılmazsa müşteri şaşırır (§8).
- **Domain sözlüğüne TimescaleDB native terimleri eklendi:** Hypertable, Chunk, Continuous Aggregate, Compression Policy, Retention Policy, Auto-Resolution Query, Parquet Archive (§3.3).

**Mimari etki:** İki süreçli mimari (`custos.critical` + analytics + dashboard) korundu. F11 yalnızca veri katmanı altyapısını derinleştirdi: 1 yeni migration + 2 continuous aggregate + auto-resolution dispatcher + Parquet arşivleyici + retention UI + collector paralelleştirme + query guard. Critical loop'a ML eklenmedi; bağımlılık minimum tutuldu (CLAUDE.md kuralı).

**Stratejik etki:** Veri artık sadece anlık izleme aracı değil, **AR-GE / ESG / sürdürülebilirlik için biriken bir varlık**. Üç paralel vizyon açıldı: ekipman ömür döngüsü analizi, enerji verimliliği / CBAM hazırlığı, arıza öngörüsü için feature engineering. Dördüncü vizyon (çapraz-tenant anonim benchmark, "Custos Benchmark") v1.1+ adayı.

**Kapsam kesme disiplini:** v1.6'daki kural geçerlidir. Ücretli pilot zemininde "yeni istek = v1.1 backlog" katı uygulanır.

---

## 1. Ürün tanımı

Custos üç katmanlı bir ürün olarak konumlanır:

**Katman 1 — Anlık izleme:** Ticari veya endüstriyel tesislerde **Modbus TCP** üzerinden sensör verisi okuyan, bu verileri endüstri standardı asset şablonlarına bağlayan, KPI hesaplayan, ML tabanlı anomali tespiti ve eşik alarmı üreten, bakım süreçlerini yöneten ve teknik bilgi tabanı üzerinden operatör ve teknik servise asistan görevi gören, **lokal çalışan bir edge izleme sistemidir**.

**Katman 2 — Lokal historian:** Yüksek çözünürlüklü (saniyelik) sensör verisinin uzun vadeli (365 gün ham + 3 yıl dakika + sınırsız saat) lokal saklandığı, geriye dönük inceleme ve trend analizi için her zaman erişilebilir veri deposu. "Trend sistemi yok ya da görünmez" olan işletmelere veri kaydı + geçmiş inceleme değer önerisi.

**Katman 3 — AR-GE veri temeli (vizyon, v1.1+):** Saha verisinin 3-5 yıl biriktiği, gelecekte ML modelleri / agent'lar / sürdürülebilirlik raporları / sektör benchmark'ı üretilecek varlık. Bugün bir ürün değil, bugünden kilit taşları atılması gereken bir gelecek opsiyonu.

**Temel kural değişmedi:** Sistem **sadece okur, asla yazmaz.** Prosese veya binaya müdahale yoktur. Modbus client kodunda yazma fonksiyonları implement edilmemiştir.

**Veri kalır, dışarı çıkmaz:** Cloud sync yok, S3 yok, NAS sync yok. Veri lokal mini PC'de tutulur. Müşteri onaylı VPN yalnızca bakım/destek için (geliştirici uzaktan sisteme bağlanır, dosya aktarımı yapmaz).

**AVM pilotu için tek cümlelik değer önerisi:** *"Binanızın HVAC ve mekanik sistemlerinin verilerini okuyan, teknik servisin anladığı dilde özetleyen, problem olmadan önce uyarı veren, ne yapılacağını adım adım söyleyen ve verilerinizi yıllarca lokal saklayan bir akıllı göz."*

**Fabrika pilotu için değer önerisi** (v1.5'ten korunmuştur): *"Fabrikanızın verilerini okuyan, sizin anladığınız dilde özetleyen, problem olmadan önce size haber veren ve ne yapmanız gerektiğini söyleyen lokal bir akıllı göz."*

**Ortak satış dili:** *"Verileriniz dışarı çıkmaz, sizde kalır, ileride sizin için değer üretecek varlığa dönüşür."*

---

## 2. Pilot müşteriler

İki pilot kurgusu vardır. AVM asıl pilot, fabrika ikinci pilot.

### 2.1. Pilot 1 — AVM (asıl pilot)

**Profil:**
- Sektör: Ticari bina / AVM (alışveriş merkezi)
- Haberleşme: Modbus TCP (yeni kurulumla gelecek — mevcut otomasyon yenileniyor)
- Otomasyon altyapısı: **Regin PLC + Expo SCADA**, kurulumu Custos geliştiricisi ve ortağı tarafından yapılacak (yeşil alan avantajı)
- İzlenecek ekipmanlar (tahmini, §4.10'da netleşecek): Chiller, AHU, Fan Coil Unit, Cooling Tower, Booster Pump Set, Enerji sayaçları, muhtemelen su tankları ve yangın pompası seti
- Ticari model: **Ücretli pilot**
- Teslim tarihi: **5 Haziran 2026 (Cuma)**
- Saha erişim kısıtı: Bina 7/24 açıktır; kurulum ve devreye alma için dar pencereler (gece veya düşük yoğunluk saatleri) kullanılacaktır.

**Pilot başarı kriteri (AVM):**
- Kurulum gününden itibaren sistem kesintisiz çalışır (uptime ≥ %99 pilot süresince)
- Teknik servis dashboard'u günlük kullanır
- En az bir gerçek uyarı üretir (eşik veya anomali)
- En az bir asset instance (örn. bir AHU veya chiller) şablonla bağlanmış haldedir
- Chatbot en az bir teknik soruya bilgi tabanından doğru cevap verir
- En az bir bakım checklist'i alarm sonrası teknik servise sunulmuştur
- **Yeni (v1.7):** Pilot süresince üretilen tüm ham veri 365 gün boyunca saklanır; geriye dönük 30 günlük chart sorgusu < 200 ms cevaplanır (auto-resolution).
- **Yeni (v1.7):** Pilot ayı dolduğunda Parquet arşivi otomatik üretilir; müşteri klasörden dosyaya erişebilir.
- Müşteri pilot bitiminde yazılı olumlu geri bildirim verir (ücretli pilot ölçütü)

**Otomasyon bağlamı:** AVM'nin mevcut otomasyonu yenilenmektedir. Regin kontrolör ve Expo SCADA kurulumunu Custos geliştiricisi ve ortağı yapmaktadır. Bu durum üç açıdan avantaj sağlar:
1. Modbus map'i sıfırdan biz tanımlıyoruz — Auto-Scan verileri temiz gelir, register layout tahmin edilebilir.
2. Bilgi tabanı dokümanları birinci elden yazılabilir — sistemi kuran kişi dokümanı da yazar.
3. Tag → role eşleme pilot öncesinde planlanabilir — Binding Wizard'ın ilk kullanım deneyimi daha yumuşaktır.

### 2.2. Pilot 2 — Fabrika (ikinci pilot, ertelendi)

**Profil:**
- Sektör: Üretim fabrikası
- Haberleşme: Modbus TCP (mevcut)
- Otomasyon altyapısı: Regin + Expo SCADA (yenilenecek — Custos geliştiricisi ve ortağı tarafından)
- Sensör çeşitliliği: Yüksek (sıcaklık, basınç, akış, seviye, motor akımı/frekans, muhtemelen titreşim ve enerji sayaçları)
- Teslim tarihi: **~Temmuz 2026** (kesin tarih AVM pilot sonuçlarına göre netleşecek)

**Pilot başarı kriteri (Fabrika):** v1.5'teki kriterler aynen geçerlidir — AVM pilotundan öğrenilen derslere göre güncellenebilir.

**Her iki pilot için ortak teslim varsayımları (v1.7'de güncellendi):**
- Donanım: Fansız N100/N200 Mini PC, 16 GB RAM, **2 TB NVMe SSD** (v1.6'daki 500 GB'dan yükseltildi — historian için yer açıyor)
- Deployment: Docker Compose + systemd
- Erişim: Lokal ağ üzerinden (`http://custos.local` mDNS veya statik IP)
- Uzak destek: Müşteri onaylı VPN, sadece bakım amaçlı, veri sync yok

---

## 3. Domain sözlüğü (Glossary)

Kod, template, dokümantasyon ve arayüzde aşağıdaki terimler kullanılacaktır.

### 3.1. Çekirdek terimler (v1.5'ten korunmuştur)

| Terim | Türkçe karşılığı | Kod isimlendirmesi |
|---|---|---|
| **Tag** | Sensör / ham veri noktası | `tag`, `tag_id` |
| **Tag Reading** | Tag'in tek bir okunması | `tag_reading` |
| **Asset Template** | Proses/ekipman şablonu | `asset_template` |
| **Asset Instance** | Somut proses/ekipman (örn. "AHU-1" veya "Sirkülasyon Pompası #1") | `asset_instance` |
| **Template Role** | Şablonun beklediği tag rolü | `role`, `role_key` |
| **Tag Binding** | Bir tag'in bir asset rolüne atanması | `tag_binding` |
| **Scaling** | `real = raw * gain + offset` dönüşümü | `gain`, `offset` |
| **Threshold** | Alarm eşiği | `threshold` |
| **Alarm State Machine** | ISA-18.2 alarm yaşam döngüsü | `alarm_state` |
| **Debouncing** | Eşik aşımının X saniye sürmesi şartı | `debounce_seconds` |
| **Hysteresis** | Alarm temizleme için ölü bant | `hysteresis` |
| **KPI Definition** | Şablon bazlı hesaplama tanımı | `kpi_definition` |
| **Computed Tag** | Diğer tag'lerden türetilen tag | `computed_tag` |
| **Anomaly Detection** | ML tabanlı sapma tespiti | `anomaly_detector` |
| **Tag Status** | Tag yaşam döngüsü durumu | `tag_status` |
| **Polling Interval** | Tag'in okunma sıklığı (ms) | `polling_interval_ms` |
| **Polling Preset** | Hazır okuma hız seviyesi | `polling_preset` |
| **Slave Latency** | Modbus slave cevap süresi | `slave_latency_ms` |
| **Fast Polling Budget** | Sistem genelinde fast tag sınırı | Pilot için maksimum 10 |
| **Maintenance Schedule** | Periyodik bakım takvimi | `maintenance_schedule` |
| **Maintenance Checklist** | Bakım/arıza kontrol listesi | `maintenance_checklist` |
| **Maintenance Task** | Tek bir bakım görevi | `maintenance_task` |
| **Knowledge Base** | Teknik bilgi tabanı (dokümanlar) | `knowledge_base` |
| **Knowledge Article** | Bilgi tabanındaki tek bir doküman | `knowledge_article` |

### 3.2. HVAC / AVM terimleri (v1.6'da eklendi)

| Terim | Açıklama | Kod isimlendirmesi |
|---|---|---|
| **Air Handling Unit (AHU)** | Havalandırma santrali | `ahu` |
| **Fan Coil Unit (FCU)** | Fan coil cihazı | `fcu` |
| **Cooling Tower** | Soğutma kulesi | `cooling_tower` |
| **Booster Pump Set** | Su basınçlandırma pompa seti | `booster_set` |
| **Energy Meter** | Elektrik sayacı | `energy_meter` |
| **Supply Temperature** | Üflenen akışkan sıcaklığı | `supply_temp` |
| **Return Temperature** | Geri dönen akışkan sıcaklığı | `return_temp` |
| **Setpoint** | Hedef değer (sıcaklık, basınç, vb.) | `setpoint` |
| **VFD** | Variable Frequency Drive (frekans invertörü) | `vfd` |
| **COP** | Coefficient of Performance (verim katsayısı, chiller) | `cop` |
| **Approach Temperature** | Cooling tower yaklaşma sıcaklığı | `approach_temp` |
| **Damper Position** | Damper açıklık yüzdesi | `damper_position` |
| **Filter ΔP** | Filtre basınç farkı | `filter_dp` |

Not: Asset template rol anahtarlarının kesin listesi §4.10'da oluşturulacaktır.

### 3.3. Historian / TimescaleDB terimleri (yeni — v1.7)

| Terim | Açıklama | Kod isimlendirmesi |
|---|---|---|
| **Hypertable** | TimescaleDB'nin zaman serisi tablo soyutlaması (otomatik chunk'lanır) | `tag_readings`, `features` |
| **Chunk** | Hypertable'ın belirli zaman aralığındaki bölümü (1 gün) | `chunk_interval` |
| **Compression Policy** | Belirli yaştaki chunk'ları sıkıştıran TimescaleDB native job | `policy_compression` (7 gün) |
| **Retention Policy** | Belirli yaştan eski chunk'ları silen TimescaleDB native job | `policy_retention` (365 gün ham) |
| **Continuous Aggregate (CA)** | Materialized view + refresh policy ile sürekli güncellenen agregat | `tag_readings_1min`, `tag_readings_1hour` |
| **Auto-Resolution Query** | Pencere büyüklüğüne göre doğru katmandan okuyan dispatcher | `query_readings_auto` |
| **Bucket** | `time_bucket(interval, ts)` ile oluşturulan agregat aralığı | `bucket_seconds` |
| **Parquet Archive** | Aylık columnar snapshot (`/var/custos/archive/YYYY-MM/`) | `ParquetArchiver` |
| **Disk Telemetri** | Dashboard'da disk doluluk widget'ı + push uyarı | `DiskMonitor` |
| **Query Guard** | Kötü/aşırı sorguları reddeden veya katmana zorla düşüren koruma | `query_guard.evaluate_query` |
| **Resolution Hint** | Chart başlığındaki "ham/dakika/saat" rozeti | `_resolution_hint_for` |

---

## 4. MVP Feature Listesi

v1.5'teki 24 feature aynen kalır, üstüne bakım modülü (F8a), chatbot (F8b), AVM template pack (F9), AVM deploy (F10) ve historian stack (F11) eklenir. v1.5/v1.6'da yazılan bölümler burada kısaltılmıştır — tam tanımlar önceki briefler'de mevcuttur ve aynen geçerlidir.

### 4.1. Veri toplama katmanı

1. **Manuel Tag Ekleme** (v1.5 §4.1 ile aynı)
2. **Modbus Auto-Scan + Aktivasyon Akışı** (v1.5 §4.1 ile aynı)
2.5. **Polling Preset Sistemi** (v1.5 §4.1 ile aynı)
3. **Tag Browser** (v1.5 §4.1 ile aynı)

**Yeni (v1.7, F11 Paket G):** Collector per-host paralelleştirildi (`asyncio.gather` + bounded semaphore N=5). Fast Polling Budget artık warn değil **`ValueError raise` (FastPollingBudgetError)** — tag aktivasyon noktasında reddediliyor; UI'da net mesaj.

### 4.2. Asset sistemi

4. **Asset Template Library** — v1.5'teki 6 fabrika şablonu (Pompa, Chiller, Plate Heat Exchanger, Hava Kompresörü, Generic Motor, Generic Tank) korunur. **AVM template'leri F9'da eklenecektir** (§4.10).
5. **Binding Wizard** (v1.5 §4.2 ile aynı)
6. **Asset Instance Yönetimi** (v1.5 §4.2 ile aynı)

### 4.3. KPI ve analitik
7. **KPI Motoru** — Çekirdek aynı. AVM template'leri için KPI formülleri §4.10'da tanımlanacaktır.
8. **KPI Sayfası** (v1.5 §4.3 ile aynı)
9. **ML Anomaly Detection** (v1.5 §4.3 ile aynı)

### 4.4. Alarm sistemi

10. **Threshold CRUD** (v1.5 §4.4 ile aynı)
11. **ISA-18.2 Alarm State Machine** (v1.5 §4.4 ile aynı)
12. **Alarm Sayfası** (v1.5 §4.4 ile aynı)

### 4.5. Bildirim

13. **Web Push Notifications** (v1.5 §4.5 ile aynı). **Yeni (v1.7, F11 Paket F):** Disk %85 dolduğunda otomatik push uyarı, 6 saat in-memory cooldown.
14. **Bildirim Ayarları Sayfası** (v1.5 §4.5 ile aynı)

### 4.6. Dashboard sayfaları

15–23. Overview, Sensors, Processes, KPI, Alarms, Logs, Settings, Maintenance, Assistant sayfaları — v1.5 §4.6 ile aynı.

**Yeni (v1.7, F11 Paket D):** Overview ve detail handler'ları `query_readings_auto`'ya geçti; tüm chart sorguları `asyncio.gather(*)` ile paralel. Chart başlığında **resolution hint badge** ("ham/dakika/saat") + uzun pencerede **"Uzun pencere — saatlik agregat"** uyarı rozeti.

**Yeni (v1.7, F11 Paket F):** Settings sayfasında "Veri Saklama" bölümü (retention seçici, auto-clean anahtarı, "Şimdi arşivle" butonu). Overview'da disk doluluk widget'ı (HTMX `hx-trigger="every 30s"`).

### 4.7. Tasarım

24. **Visual Language** (v1.5 §4.7 ile aynı)

### 4.8. Bakım modülü (F8a — AVM'de de geçerli)

v1.5 §4.8 + v1.6 notları aynen geçerlidir. F8a 19 Nisan 2026'da tamamlandı (5 commit, dfb01e1 → a31de29).

### 4.9. Teknik asistan modülü (F8b → genişletildi, 2026-05-28)

> **Kapsam değişikliği (2026-05-28):** Aşağıdaki tanım, v1.5 §4.9'daki dar kapsamı (yalnızca dahili Markdown/YAML bilgi tabanı + sohbet UI) **supersede eder**. Asistan, pilot öncesi demo silahı olarak öne çekildi (AVM pilotu ≥2 ay ertelendi); önceliklendirme gerekçesi `docs/custos_asistan_is_plani_v1.md`. Mevcut F8b semantic altyapısı (embedding + FAISS + retriever) yeniden kullanılır; sohbet UI'ı görsel arama UI'ı ile **değiştirilir**.

**Hedef:** İşletmenin kendi teknik ekipman **PDF manuellerini** yükleyip, vardiyadaki teknisyenin Türkçe/İngilizce soru yazıp **orijinal manuel sayfasını** saniyeler içinde görsel olarak bulabildiği, LLM'siz, %100 offline çalışan teknik asistan.

**Deterministik felsefe (Custos ana ürün ilkesiyle aynı):** LLM yok, AI sentezi yok. Asistan cevap *üretmez*; ilgili **orijinal sayfayı** gösterir. Kullanıcı gördüğü bilginin manuelin hangi sayfasından geldiğini birebir doğrular. Semantic + sparse arama yalnızca *doğru sayfayı bulmak* için kullanılır.

**Bileşenler:**
- **PDF ingest:** `pymupdf` ile sayfa-bazlı text extraction + sayfa PNG render (200 DPI). Text yoksa (taranmış PDF) `pytesseract` OCR fallback (Türkçe + İngilizce dil paketleri). pymupdf çökerse `pdfplumber` fallback.
- **Retrieval (hibrit):** dense (`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` — F8b'deki kilitli model) + sparse (`rank_bm25`, kod numarası araması için: "E102" gibi). Reciprocal Rank Fusion (RRF) ile birleştirme. Ekipman metadata filtresi (önce filtrele, sonra ara).
- **Index:** FAISS, **diske kalıcı** (F8b'de v1.1'e ertelenen persist artık zorunlu — ölçek 10+ manuel × yüzlerce sayfa).
- **Görsel UI:** arama kutusu + ekipman filtresi → sonuç sayfa thumbnail'ları → tıklayınca tam sayfa modal (zoom, sayfa no, kaynak PDF adı). Sorguya en yakın cümle(ler) sayfa üzerinde sarıyla **highlight** edilir (pymupdf native `draw_rect` + sidecar cache).

**Mimari (ayrı servis):** Asistan, Critical ve Analytics'ten bağımsız **üçüncü süreç** olarak çalışır — kendi systemd unit'i, kendi portu (`127.0.0.1:8001`), Caddy `/assistant/*` → 8001 reverse proxy, cgroup limit (`MemoryMax=2G`, `Nice=10`). Embedding/PDF işleme yükü critical ve analytics loop'larından tam izole.

**Persistence — `assistant` PostgreSQL şeması:** `documents` (filename, equipment_model/type, language, total_pages, ocr_used, source_pdf_path), `chunks` (document_id, page_no, text_content, png_path, faiss_index_id, has_table/figure), `queries_log` (query_text, result_chunk_ids, selected_chunk_id, query_time_ms — UX metriği). Erişim asistan servisinin kendi repository katmanından (ham SQL tek modülde, bkz. CLAUDE.md mimari istisnası). PNG'ler ve FAISS index diskte (`/var/lib/custos/assistant/`).

**Donanım:** N100 + 16 GB RAM yeter (LLM yok). %100 offline, internet bağımsız.

**Kapsam dışı (v1):** Custos veri entegrasyonu (asset/alarm/bakım sorguları), çok turlu diyalog, LLM cevap sentezi — pilot sonrası backlog.

### 4.10. AVM Asset Template Pack (F9 — v1.6, planlanıyor)

v1.6 §4.10 aynen geçerlidir. Template detayları saha araştırması sonrası §4.10 güncellenecek. F9 başlangıcı: F8b sonrası.

### 4.11. AVM Deploy + Saha Hazırlığı (F10 — v1.6, planlanıyor)

v1.6 §4.11 aynen geçerlidir. F10 başlangıcı: F9 sonrası.

### 4.12. Historian & Retention Stack (F11 — YENİ, v1.7, TAMAMLANDI)

**Hedef:** TimescaleDB native özellikleri (chunk/compression/retention/continuous aggregate) ile lokal historian altyapısının kurulması. Ham veriden Parquet arşivine kadar tüm veri katmanları, auto-resolution query API, dashboard entegrasyonu, retention UI, collector paralelleştirme ve query guard. 8 paket (A-H), tamamlanma: 21 Nisan 2026.

**Kanonik kaynaklar:**
- `docs/custos_altyapi_vizyon_ozeti_v1.md` (vizyon + mimari kararlar)
- `docs/custos_is_plani_v1.md` (haftalık dağılım + paket detayları)

**Veri katmanları (vizyon §2.1):**

| Katman | İçerik | Saklama süresi | Yaklaşık boyut (200 tag × 1/s) |
|---|---|---|---|
| **Ham tablo** (`tag_readings`) | Saniye çözünürlüklü okumalar | **365 gün** | ~90 GB |
| **Dakika agregat** (`tag_readings_1min`) | AVG/MIN/MAX/STDDEV/COUNT | **3 yıl** | ~1 GB |
| **Saat agregat** (`tag_readings_1hour`) | AVG/MIN/MAX/STDDEV (1min'den hierarchical) | **Sınırsız** | ~50 MB / 10 yıl |
| **Parquet arşiv** | Aylık snapshot — columnar, LZ4 | **Sınırsız** (müşteri isterse siler) | ~10 GB / yıl |

**Varsayılan = cömert.** Müşteri Settings'ten ham süreyi 30/60/180/365 güne kısaltabilir veya "auto-clean off" yapar.

**Paket A — TimescaleDB Production Hardening** *(commit `5239b6b`)*
- Migration 024: `tag_readings` + `features` hypertable'larına chunk interval = 1 gün, `ALTER TABLE ... SET (timescaledb.compress, compress_segmentby='tag_id', compress_orderby='timestamp DESC')`, compression policy = 7 gün, retention policy = 365 gün.
- Verification script: `scripts/verify_timescale_policies.py` (4 kontrol + chunk_stats info).
- 5 integration testi (`tests/integration/test_timescale_hardening.py`).
- TimescaleDB 2.25 nuance: `compression_settings` per-column row, `jobs.config` JSON string (asyncpg jsonb codec yok).

**Paket B — Continuous Aggregates** *(commit `244c790`)*
- Migration 025: `tag_readings_1min` (5 dk refresh, 3 yıl retention) + `tag_readings_1hour` (1min'den hierarchical, 30 dk refresh, retention yok).
- Backfill scripti: `scripts/refresh_continuous_aggregates.py` (`CALL refresh_continuous_aggregate` açık txn'da çalışamadığı için migration'a gömülemedi).
- 6 integration testi.
- Not: 1hour STDDEV yaklaşık (60 dakikanın ağırlıksız AVG'si, exact pooled-variance değil — kabul edildi).

**Paket C — Auto-Resolution Query API** *(commit `2ab121d`)*
- `DatabaseInterface.query_readings_auto(tag_id, start, end, target_points=600)`: inclusive eşikler `<=1h` ham, `<=1d` 1min, `>1d` 1hour.
- 3 private helper: `_query_raw_downsampled`, `_query_1min_downsampled`, `_query_1hour_downsampled`.
- Bucket adayları: raw serbest sn, 1min `1/5/10/15` dk, 1hour `1/3/6/12` h. `_pick_bucket(desired, candidates)` → `ceil(window/target)` listedeki en küçük eşit-veya-büyük.
- Homojen `list[TagReading]`; `value=AVG`, `quality_flag=MAX`.
- `query_tag_readings_downsampled` `[DEPRECATED F11]` işaretli; v1.1'de silinecek.
- `DEFAULT_TARGET_POINTS = 600` modül sabiti.
- 8 integration testi (6 core + 2 boundary: tam 1h → raw, tam 1d → 1min).

**Paket D — Dashboard Auto-Resolution + Gather Paralelleştirme** *(commit `001850f`)*
- Overview handler: iç içe seri loop → tek seviyeli task list + `asyncio.gather` (chart × tag tüm sorgular paralel).
- `query_tag_readings_downsampled` → `query_readings_auto` geçişi (overview + detail).
- Resolution hint badge: `chart_panel` macro'da opsiyonel parametre + detail başlığı, `data-resolution-hint="ham/dakika/saat"` attribute.
- `_resolution_hint_for(timedelta)` saf helper, eşikler `query_readings_auto` ile aynı.
- 8 dashboard testi (parallel `asyncio.Event` gate ile kanıt, parametrize badge, helper saflığı).
- Smoke test: overview 27-63 ms, detail 10-12 ms (3 chart × 4 tag = 12 paralel sorgu).

**Paket E — Parquet Aylık Arşiv Job** *(commit `ddf8120`)*
- `pyarrow>=15.0,<20.0` dependency (kullanıcı onayı 20 Nisan).
- `archiver.py`: `ParquetArchiver`, `ArchiveResult`, TRT ay sınırları, LZ4 sıkıştırma. Stream cursor + prefetch ile bellekte tutmadan yazım.
- `archive_scheduler.py`: Asyncio tick (5 dk), her ayın 1'inde 02:00 TRT'de bir önceki ay arşivlenir.
- Manuel tetik endpoint: `POST /dashboard/api/archive/run` + module-level `asyncio.Lock` (eşzamanlı istekte 409 Conflict).
- Dizin yapısı: `/var/custos/archive/YYYY-MM/{tag_readings,tag_readings_1min,tag_readings_1hour}.parquet`
- 15 test (8 integration + 7 unit).
- `setup.sh`: `/var/custos/archive` dizin + `chown` + `chmod 750`.

**Paket F — Retention UI + Disk Telemetri** *(commit `43df66e`)*
- Migration 026: `retention_config` singleton tablosu (varsayılan 365 gün + auto_clean_enabled = true).
- `RetentionConfig` dataclass + `get_retention_config` / `update_retention_config` (transaction içinde hem `tag_readings` hem `features` policy'sini senkronlar; off = remove; on = remove+add).
- `disk_telemetry.py`: `get_disk_usage` (shutil wrap) + `DiskMonitor` (5 dk tick, %85 eşikte push warn, 6 saat in-memory cooldown).
- Dashboard endpoint'leri: `POST /settings/retention` (radio form, audit log) + `GET /api/disk-usage` (HTMX partial).
- Settings sayfasında "Veri Saklama" bölümü: disk widget, radio 30/60/180/365/Sınırsız, auto-clean uyarısı, "Şimdi arşivle" butonu.
- Overview'da disk doluluk widget'ı (`hx-trigger="every 30s"`).
- 14 test (5 retention + 5 disk + 4 UI).

**Paket G — Collector Paralelleştirme + Budget Enforcement** *(commit `40da3b5`)*
- `collector.py`: `_run_tick` due_tags `(host, port)` ile grupluyor; `_read_host_group` her grubu `Semaphore(N) + gather` ile paralel, hostlar arası da `gather`.
- `FastPollingBudgetError(ValueError)` + init-time enforcement.
- `slow_tick_ratio` / `total_tick_count` metrik property'leri.
- Settings: `collector_per_host_concurrency=5`, `collector_fast_polling_budget=10`.
- Dashboard: `_count_active_fast_tags` helper. `sensor_create` ve `sensor_update`'te budget aşımında HTTP 400 + "Fast polling bütçesi dolu (X/Y)…". Form'da bütçe rozeti.
- 6 test (4 enforcement + 2 simülatörlü yük).
- 100 tag × 1 Hz yük testi: `slow_tick_ratio < %5` (geçti).
- Not: Tek host + tek TCP socket'te paralel/sequential ratio ~0.98–1.02 (pymodbus içeriden queue ediyor); kazanım çoklu host + N-client pool'da. v1.1 backlog'a "N-client pool".

**Paket H — Query Guard** *(commit `811821b`)*
- `query_guard.py`: pure `evaluate_query` + `GuardDecision` + `QueryGuardError` (eşikler Settings'ten override edilebilir).
- `query_readings_auto`'ya `tag_count` parametresi + guard çağrısı: forced katman override + reject → exception.
- Dashboard: overview + detail handler'da `try/except QueryGuardError` → HTTP 400. `_is_long_window` helper + context'e `long_window_hint`.
- `chart_panel` macro: `status-warn` tonlu **"Uzun pencere — saatlik agregat"** rozeti.
- 6 test (4 pure matris + 1 entegrasyon + 1 dashboard 400).

**F11 toplam istatistik:**
- 7 commit (`5239b6b` → `811821b`)
- 3 alembic migration (024 hardening, 025 CA, 026 retention_config)
- ~3-4 iş günü efor (planda 5-6 gündü, mevcut feature ile paralel ilerletildi)
- 60+ yeni test (5 + 6 + 8 + 8 + 15 + 14 + 6 + 6)

**F11 olmayan / scope dışı (vizyon §2.4 + iş planı):**
- Cloud sync / S3 / NAS yok — tamamen lokal.
- Otomatik Parquet silme yok — kullanıcı manuel siler.
- Çoklu N-client pool (collector) — v1.1 backlog.
- BACnet/IP — v1.1 backlog.

---

## 5. Teknik mimari

### 5.1. Stack (kilitli — v1.5'ten değişmedi, v1.7'de pyarrow eklendi)

- **OS:** Ubuntu 24.04 LTS
- **Backend:** Python 3.12 + FastAPI + Uvicorn
- **Veritabanı:** PostgreSQL 16 + TimescaleDB 2.25
- **Modbus:** `pymodbus` (<3.13.0 pinli)
- **Template engine:** Jinja2
- **Frontend:** HTMX 2.0 + Alpine.js 3.14 + uPlot 1.6
- **CSS:** Tailwind 3.4 standalone binary
- **ML:** scikit-learn, numpy, pandas
- **Semantic Search:** sentence-transformers (paraphrase-multilingual-MiniLM-L12-v2), faiss-cpu
- **Background jobs:** APScheduler / asyncio task
- **Web Push:** `pywebpush`
- **Parquet (yeni — v1.7):** `pyarrow>=15.0,<20.0`

### 5.2. İki süreçli mimari (v1.5'ten değişmedi)

- **`custos.critical`** — Collector + Threshold Engine + Alarm dispatcher
- **`custos.analytics`** — Feature engine + ML + KPI + Dashboard + Web Push + Bakım zamanlayıcı + Chatbot + **Parquet arşiv scheduler** + **Disk monitor** (yeni — v1.7)
- **`custos.shared`** — Config, logging, database abstract interface, domain models, **historian helper'ları (`query_guard`, `archiver`, `disk_telemetry`)** (yeni — v1.7)

### 5.3. Donanım (pilot için — v1.7'de güncellendi)

- Intel N100/N200 fansız Mini PC, 16 GB RAM, **2 TB NVMe SSD**, Gigabit Ethernet
- v1.6'daki 500 GB'dan 2 TB'a yükseltme. Ek maliyet ~80–150 €. Historian (365 gün ham × 200 tag × 1 Hz = ~90 GB) + Parquet arşiv (~10 GB/yıl) + sistem + log + buffer için yer.
- Teklife açık kalem: "Yüksek çözünürlüklü 1 yıllık veri saklama için 2 TB SSD".

### 5.4. Deployment (v1.5'ten değişmedi)

- Docker Compose + systemd, lokal ağ erişimi (`http://custos.local`)

### 5.5. Bilgi tabanı dosya yapısı (v1.5'ten değişmedi, AVM içeriği eklenecek)

v1.5 §5.5'teki yapı aynen geçerlidir.

### 5.6. Çoklu kurulum (v1.6'dan korundu)

AVM ve fabrika **iki ayrı kurulum**tur. Multi-tenant yok. Kod tabanı tek, config/data farklı, Docker Compose `.env` ile farklılaşır. Uzak destek için VPN bazlı erişim; her iki sahanın VPN endpoint'i ayrı.

### 5.7. Veri katmanları + Parquet arşiv (yeni — v1.7)

**Tablolar ve hypertable yapısı:**
- `tag_readings` (hypertable, chunk = 1 gün, compression policy 7 gün, retention 365 gün)
- `tag_readings_1min` (continuous aggregate, refresh 5 dk, retention 3 yıl)
- `tag_readings_1hour` (continuous aggregate, 1min'den hierarchical, refresh 30 dk, retention yok)
- `features` (hypertable, aynı politikalar — feature engineering çıktıları)

**Auto-resolution query API:**
- `query_readings_auto(tag_id, start, end, target_points=600, tag_count=1)`: pencere genişliğine göre otomatik katman seçer (`<=1h` ham, `<=1d` 1min, `>1d` 1hour). Bucket re-pick (`_pick_bucket`) ile dönen nokta sayısı `target_points`'i nadiren 1-2 aşar (epoch-aligned).
- Tüm katmanlar homojen `list[TagReading]` döndürür: `value=AVG`, `quality_flag=MAX`. Tüketici taraf katman değişiminden etkilenmez.

**Parquet arşiv:**
- **Yer:** `/var/custos/archive/YYYY-MM/` — mini PC içinde müşterinin erişebildiği klasör (chmod 750)
- **Sıklık:** Aylık. Her ayın 1'inde 02:00 TRT'de bir önceki ay arşivlenir (asyncio scheduler 5 dk tick).
- **Manuel tetik:** Settings sayfasında "Şimdi arşivle" butonu (`POST /dashboard/api/archive/run` + `asyncio.Lock`).
- **İçerik:** Ham + 1min + 1hour — ayrı Parquet dosyaları (LZ4 sıkıştırma, μs hassasiyet).
- **Amaç:** Müşteriye "verim dosya olarak elimde" güveni + TimescaleDB'den bağımsız okunabilir format (Apache Arrow ekosistemi 20 yıl sonra da yaşar).
- **Veri sync YOK:** Cloud, S3, NAS hiçbirine gönderilmez.

**Retention UI:**
- Settings → "Veri Saklama" bölümü: ham retention seçici (30/60/180/365/Sınırsız), "auto-clean off" anahtarı, "Şimdi arşivle" butonu.
- "Sınırsız" = `remove_retention_policy` (silme yok, disk dolana kadar tutar).
- `auto_clean_enabled` `tag_readings` ve `features`'i birlikte yönetir.
- Singleton tablo `retention_config` kullanıcı tercihini saklar.

**Disk telemetri:**
- Overview'da disk doluluk widget'ı (`hx-trigger="every 30s"`): yeşil <%70, sarı %70-85, kırmızı ≥%85.
- `DiskMonitor` 5 dk tick: %85 eşikte Web Push warn (6 saat in-memory cooldown).

**Query guard:**
- `query_readings_auto` içinde `(tag_count × time_range_days)` ile evaluate. Eşik aşılırsa: forced katman override (sessiz) veya reject (`QueryGuardError` → HTTP 400 dashboard).
- Chart başlığında "Uzun pencere — saatlik agregat" uyarı rozeti.

---

## 6. Aşama 4 feature sıralaması (revize — v1.7)

**Başlangıç:** F1-F7 + F8a tamamlandı.
**Bugün:** 21 Nisan 2026 (Salı).
**AVM pilot tarihi:** 5 Haziran 2026 (Cuma). Kalan süre: yaklaşık 6 hafta 3 gün.

**F11 paralel akış stratejisi (gerçekleşti):** v1.6'da W1-W7 takvimi feature başına 1 hafta varsayıyordu. F11 (Historian Stack) bu takvime paralel serpiştirildi (iş planı v1) ve **mevcut feature'lardan önce kapandı** (21 Nisan, 4 günde A-H). Bu, F8b/F9/F10 için zaman buffer'ı yarattı.

| Hafta | Tarih aralığı | Ana feature | F11 paketleri (paralel — gerçekleşti) | Durum |
|---|---|---|---|---|
| **W1** | 16–22 Nisan | F8a Bakım Modülü | — | ✅ Tamamlandı (19 Nisan) |
| **W2 başı** | 21 Nisan | — | F11 A→H (4 günde) | ✅ Tamamlandı (21 Nisan) |
| **W2** | 23–29 Nisan | **F8b: Teknik Asistan Chatbot** | regresyon + brief v1.7 | Sırada |
| **W3** | 30 Nisan – 6 Mayıs | **F9: AVM Template Pack** | — | Sırada |
| **W4** | 7–13 Mayıs | **F10: AVM Deploy + Saha Hazırlığı** | — | Sırada |
| **W5** | 14–20 Mayıs | Saha entegrasyonu 1 (Regin + Modbus map) | — | Sırada |
| **W6** | 21–27 Mayıs | Saha entegrasyonu 2 (tuning + binding) | — | Sırada |
| **W7** | 28 Mayıs – 4 Haziran | Son test + buffer | — | Sırada |
| **5 Haziran** | — | **AVM Pilot Go-Live** | — | — |

**Fabrika pilotu:** AVM go-live sonrası 2 hafta "kapı arkası" değerlendirme; Temmuz ortası fabrika entegrasyonu başlar.

**Kritik uyarı (v1.6'dan korundu):** Bu takvim hâlâ dardır. Herhangi bir feature 3+ gün gecikirse kapsam kesme kaçınılmazdır. Kesme önceliği:
1. F9'daki "ikinci öncelik template'ler"
2. Chatbot'un Custos veri entegrasyonu (sadece doküman arama ile açılabilir)
3. Bakım modülünde periyodik takvim (sadece alarm-checklist ile açılabilir)
4. **F11 son paketleri (Paket H query guard) v1.1'e ertelenebilir** (zaten "kesme önceliği 1. sıra" iş planında — ama bu pakette tamamlandı, geri çekme gerekmez)
5. Asla kesilmez: Collector, Threshold, Alarm state machine, Binding Wizard, Overview + Sensors + Processes + Alarms sayfaları, **F11 A/B/C/E paketleri** (veri katmanı ve Parquet arşivi sonradan kurulamaz, disk yapısını bozar).

Her hafta sonunda: tüm testler yeşil, ruff + mypy temiz, commit mesajları Türkçe, git log düzgün, demo-able durum.

---

## 7. Çalışma kuralları

v1.5 §7 + v1.6 §7'deki 12 kural aynen geçerlidir. Altı çizili nokta:

- **Kural 9 "Brief değişirse versiyon artar."** Bu versiyon (v1.7) F11 stack tamamlanması nedeniyle yazılı kararla açılmıştır.
- **Kural 10 "Minimum hareketli parça."** AVM pilotuna 6 hafta var; yeni bağımlılık eklenmeyecek (`pyarrow` istisna — F11 için onaylı).
- **Kural 11 "Scope kesme önceliği belgelenmiştir."** §6 güncel.
- **Kural 12 "AVM pilotu ücretli pilottur."** Her feature kapanışında "müşteri 5 Haziran'da gerçek kullanımla deneyecek" sorusu sorulur.

Yeni — v1.7'ye özel:

13. **Cömert retention default.** Veri saklama varsayılanları her zaman cömert (365 gün ham + 3 yıl dakika + sınırsız saat + Parquet arşiv). Kısıtlama müşterinin bilinçli kararıdır. UI'da retention seçici "Sınırsız" seçeneği daima mevcuttur. Müşteri ne kadar süre tutacağını seçer; geliştirici default'tan emin olur.

---

## 8. Bilinen riskler ve azaltımları

v1.6'daki riskler korundu + F11 / historian ile ilgili yeni risk eklendi.

| Risk | Olasılık | Etki | Azaltım |
|---|---|---|---|
| **Disk büyüme hızı — retention UX'i yeterince net anlatılmazsa müşteri şaşırır** (yeni, v1.7) | Orta | Orta | Settings'te disk widget %70/%85 eşikleri; %85'te otomatik push uyarı; "Veri Saklama" bölümünde retention seçici net etiketli; pilot eğitiminde retention konuşulur. Default = 365 gün ham (90 GB / 200 tag), 2 TB SSD bunu bolca taşır. |
| **Parquet arşiv günü disk dolarsa job patlar** (yeni, v1.7) | Düşük | Orta | `archive_month` her dosyayı stream cursor ile yazar (bellekte tutmaz). Gerekirse retention politikası daraltılır. Manuel endpoint operatöre kontrol verir. Pilot deploy'a kadar büyük ay smoke testi yapılmadı (kabul edilen risk). |
| **TimescaleDB compression policy 7 günden önce çalışırsa ham sorgu yavaşlar** (yeni, v1.7) | Düşük | Düşük | Compression policy 7 gün after sabitlendi; auto-resolution query >1h pencere için zaten 1min/1hour CA'ya düşer. Ham sorgu sadece <=1h penceresinde, o da en yeni veri (compressed olmaz). |
| **7 haftalık takvim sürtünmeyle kayar** (v1.6) | Yüksek (azaldı — F11 erken bitti) | Çok Yüksek | §6'daki kesme önceliği; F11 buffer'ı F8b/F9/F10'a aktardı. |
| **AVM 7/24 çalışıyor, kurulum penceresi dar** (v1.6) | Yüksek | Orta | Gece kurulum; laboratuvarda ön hazırlık. |
| **Regin PLC map'i gecikir** (v1.6) | Orta-Yüksek | Yüksek | Ortak ile haftalık sync; W3 sonu register map donar. |
| **AVM bilgi tabanı dokümanları zamanında yazılmaz** (v1.6) | Orta-Yüksek | Orta | Minimum 10 makale W3'te; chatbot boş tabanla da çalışır. |
| **Ücretli pilot — müşteri memnun kalmazsa ticari risk** (v1.6) | Orta | Yüksek | Haftalık check-in; W5 sonu erken demo. |
| Modbus Auto-Scan yetersiz | Düşük | Orta | AVM'de map'i biz tanımladığımız için risk düşük. |
| Eski PLC fast polling'i kaldıramaz | Düşük | Orta | Fast Polling Budget = 10 (Paket G'de raise enforcement). |
| Pilot kurulum günü Modbus bağlantı sorunları | Orta | Yüksek | Connection Diagnostic F10; full dry-run. |
| ML modeli ilk gün anlamlı sonuç üretmez | Yüksek | Düşük | "Öğrenme süresi" iletişimi; eşik alarmı öne çıkar. |
| Mini PC ısınma | Düşük-Orta | Orta | Fansız N100/N200; AVM IT odası kontrollü. |
| Müşteri yeni feature ister | Yüksek | Orta | v1.1 backlog; her istek yazılı. |
| Tek kişilik tempo | Yüksek | Yüksek | Sabah/akşam check-in; ortak ile paralel. |
| Sentence-transformers Türkçe kalitesi | Düşük-Orta | Orta | Multilingual model; YAML Q&A fallback. |
| Embedding modeli N100'de yavaş | Düşük | Düşük | Model bir kez yüklenir; ~50-100 ms. |

---

## 9. Pilot sonrası (v1.1+) backlog

v1.5 + v1.6 §9'daki liste geçerlidir. F11 kapsamından çıkan / ertelenmiş yeni adaylar:

- **N-client Modbus pool** (collector) — Tek TCP socket'ten N socket pool'a geçiş; gerçek paralel kazancı için (Paket G notu).
- **Cloud sync / müşteri kontrollü yedekleme** — Opsiyonel, müşteri kontrolünde (vizyon §2.4).
- **Multi-site / multi-tenant** (AVM + fabrika tek dashboard talebi)
- **BACnet/IP desteği** (AVM otomasyon dünyasında yaygın)
- **Kiracı bazlı enerji raporlaması**
- **Vardiya bazlı alarm yönlendirme**
- **Mobil bildirim (PWA veya native)**
- **Chatbot çok turlu diyalog**
- **Bakım geçmişi raporları (PDF export)**
- **Enerji verimliliği raporları** (AVM için kritik — F11 historian altyapısı buna hazır)
- **"Custos Benchmark" — ikinci ürün vizyonu** (çapraz-tenant anonim aggregate katmanı; vizyon §3 + iş planı Akış 4)
- **Settings UI üzerinden compression policy ayarı** (şu an sabit 7 gün)
- **AR-GE ESG / CBAM raporları** (yıllık AVG/MAX/STDDEV + bakım olayları korelasyonu — F11 veri temeli üstüne)

---

## 10. Sıradaki adım

**Hemen yapılacak (21–22 Nisan):**
1. **Bu briefin (v1.7) kullanıcı tarafından onaylanması.**
2. **F8b: Teknik Asistan Chatbot** feature başlangıcı (W2, 23 Nisan). Scope dar: semantic arama + chunking ("yaparsak güzel olur" modu), kapsamlı değil. *(2026-05-28: kapsam genişletildi — PDF görsel retrieval + ayrı servis; güncel tanım §4.9.)*
3. **W6 regresyonu** — Pre-existing 6 test fail'i (overview_chart_tags FK fixture, walking_skeleton timing, scanner cleanup) F11 stack'i bittiği için artık F11-bağımsız teknik borç. Hedef: `pytest tests/` → 0 fail. Önceliğe sok (paralel iş).
4. AVM template araştırması (paralel): müşteri ile teknik toplantı, ekipman listesi + approx tag sayısı + Regin map taslağı. §4.10 güncellenecek.
5. AVM bilgi tabanı doküman iskeletinin yazılması (paralel, ortak ile).

**F9 öncesi netleşmesi gerekenler (W3 başlamadan):**
- AVM ekipman kesin listesi
- Her template için rol listesi ve kardinalite
- Enerji sayacı tipi ve Modbus haritası
- Jeneratör, yangın pompası dahil mi?
- Otopark havalandırma dahil mi?
- **Yeni (v1.7):** Gerçek tag sayısı + polling mix (collector parametreleri için, vizyon §6 açık kalem)

**AVM pilot öncesi netleşmesi gerekenler (W5 başlamadan):**
- Kurulum tarihi ve saha erişim saatleri
- VPN + uzaktan destek modeli (sadece bakım, veri sync yok — vurgula)
- Müşteri tarafı iletişim kişisi ve eğitim planı
- Pilot kabul testi oturumu tarihi
- **Yeni (v1.7):** 2 TB NVMe SSD siparişi (W5 başına lazım — iş planı kritik bağımlılık #3)

**Pilot sonrası açık kalemler (vizyon §6 + iş planı Bölüm B):**
- Cloud sync / müşteri kontrollü yedekleme — v1.1 kararı
- "Custos Benchmark" — ikinci ürün vizyonu (kullanıcının paralel projesi ile kesişim)
- Ortak ile gelir paylaşımı modeli — AVM pilot sonrası
- Veri mülkiyeti sözleşmesi — Mayıs hedefi (kullanıcı + avukat)
- Şirketleşme + hibe başvuru sırası — Temmuz başı (Limited kuruluş 7-10 iş günü)

---

**Bu doküman F11 Historian & Retention Stack tamamlanması ile birlikte v1.6'dan v1.7'ye yükseltilmiştir. v1.6 brief'i AVM teklifi sonrası yazılmıştı, v1.7 ise pilot öncesi son altyapı katmanını (lokal historian) yazıya döker. Değişmesi gerekirse versiyon artırılarak revize edilir. Sessizce düzenleme yapılmaz.**
