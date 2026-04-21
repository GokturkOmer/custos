# Custos — Proje Brief

**Versiyon:** 1.6
**Tarih:** 16 Nisan 2026
**Durum:** Asıl pilot AVM (ücretli, 5 Haziran 2026). Fabrika pilotu Temmuz 2026'ya ertelendi.
**Önceki versiyonlar:** v1.0 (8 Nisan), v1.1, v1.2 (Custos adı), v1.3 (10 Nisan, pilot müşteri), v1.4 (11 Nisan, tag akışı netleştirme), v1.5 (14 Nisan, bakım modülü + chatbot)

---

## 0. Bu versiyonda ne değişti

v1.5 → v1.6 değişiklikleri:

- **Pilot müşteri ve sıralama değişti** — Yeni bir teklif geldi. **AVM (ticari bina) pilotu asıl pilot olarak öne alındı**; fabrika pilotu Temmuz 2026'ya ertelendi. Bu, v1.0'dan beri "üretim fabrikası" etrafında şekillenen briefin ilk sektör genişlemesidir.
- **Pilot teslim tarihi: 5 Haziran 2026 (AVM).** Fabrika pilotu için yaklaşık Temmuz 2026 hedefleniyor, kesin tarih sonra netleşecek.
- **Pilot ticari modeli değişti** — AVM pilotu **ücretli**. Bu, başarısızlık maliyetini artırır; scope kesme disiplini sıkılaştırılmalıdır.
- **Ürün tanımı genişledi** — Custos artık sadece üretim tesisleri için değil, HVAC ağırlıklı ticari binalar için de konumlanmaktadır. Çekirdek mimari değişmiyor; asset template kütüphanesi genişliyor.
- **Yeni asset template ailesi planlandı (F9: AVM Template Pack)** — AHU, Fan Coil Unit, Cooling Tower, Booster Pump Set, Energy Meter ve olası diğer AVM ekipmanları. **Detay liste ve rol tanımları TBD** — saha araştırması sonrası tamamlanacaktır (bkz. §4.10).
- **Yeni saha hazırlık feature'ı (F10: AVM Deploy + Saha Hazırlığı)** — Regin + Expo SCADA kurulumu ile Custos kurulumunun paralel koordinasyonu, saha devreye alma, tuning, kabul testi.
- **Aşama 4 planı revize edildi** — F8a (Bakım) ve F8b (Chatbot) kapsamda kalıyor (AVM'de de gerekli). F9 ve F10 eklendi. Takvim kalan ~7 haftaya sıkıştırıldı.
- **Multi-tenant kararı yinelendi:** Gerek yok. AVM ve fabrika için iki ayrı kurulum. v1.1+ backlog'unda kalıyor.
- **Domain sözlüğüne HVAC/AVM terimleri eklendi:** Air Handling Unit (AHU), Fan Coil Unit (FCU), Cooling Tower, Booster Pump Set, Energy Meter, Supply/Return Temperature, VFD, Setpoint.
- **Risk matrisi güncellendi** — 7/24 çalışan bina, dar kurulum penceresi, ücretli pilot, 7 haftalık sıkışık takvim, AVM ekipman keşfi (brownfield olmayabilir, Regin + Modbus map'i sıfırdan biz kuruyoruz — bu avantaj).

**Mimari etki:** Kritik/analytics/shared üç süreçli mimari aynen korunur. Yeni template'ler yalnızca `asset_template` kütüphanesine YAML/JSON seed olarak eklenir; kod yolu değişmez. KPI motoru ve ML aynı altyapıyı kullanır. Bakım modülü ve chatbot AVM için de devrededir; bilgi tabanı AVM'ye özel dokümanlarla doldurulacaktır.

**Kapsam kesme disiplini:** Süre daraldığı için aşağıdaki kural geçerlidir: *Yeni istek geldiğinde v1.1 backlog'a yazılır, MVP'ye alınmaz. Bu kural zaten vardı; v1.6'da daha katı uygulanacak.*

---

## 1. Ürün tanımı

Custos, ticari veya endüstriyel tesislerde **Modbus TCP** üzerinden sensör verisi okuyan, bu verileri endüstri standardı asset şablonlarına bağlayan, KPI hesaplayan, ML tabanlı anomali tespiti ve eşik alarmı üreten, bakım süreçlerini yöneten ve teknik bilgi tabanı üzerinden operatör ve teknik servise asistan görevi gören, **lokal çalışan bir edge izleme sistemidir**.

**Temel kural değişmedi:** Sistem **sadece okur, asla yazmaz.** Prosese veya binaya müdahale yoktur. Amaç: ekipman ve işletme koruması, erken uyarı, operasyonel görünürlük, bakım optimizasyonu, teknik servise bilgi desteği.

**AVM pilotu için tek cümlelik değer önerisi:** *"Binanızın HVAC ve mekanik sistemlerinin verilerini okuyan, teknik servisin anladığı dilde (chiller, AHU, fan coil, booster) özetleyen, problem olmadan önce uyarı veren ve ne yapılacağını adım adım söyleyen lokal bir akıllı göz."*

**Fabrika pilotu için değer önerisi** (v1.5'ten korunmuştur): *"Fabrikanızın verilerini okuyan, sizin anladığınız dilde özetleyen, problem olmadan önce size haber veren ve ne yapmanız gerektiğini söyleyen lokal bir akıllı göz."*

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
- Chatbot en az bir teknik soruya bilgi tabanından doğru cevap verir (teknik servis için)
- En az bir bakım checklist'i alarm sonrası teknik servise sunulmuştur
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

**Her iki pilot için ortak teslim varsayımları:**
- Donanım: Fansız N100/N200 Mini PC, 16 GB RAM, 500 GB NVMe SSD
- Deployment: Docker Compose + systemd
- Erişim: Lokal ağ üzerinden (`http://custos.local` mDNS veya statik IP)

---

## 3. Domain sözlüğü (Glossary)

Kod, template, dokümantasyon ve arayüzde aşağıdaki terimler kullanılacaktır. v1.5'teki endüstriyel terimler korunmuş, AVM/HVAC için yenileri eklenmiştir.

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

### 3.2. HVAC / AVM terimleri (yeni — v1.6)

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

---

## 4. MVP Feature Listesi

v1.5'teki 24 feature aynen kalır, üstüne bakım modülü (F8a), chatbot (F8b), AVM template pack (F9) ve AVM deploy (F10) eklenir. v1.5'te yazılan bölümler burada kısaltılmıştır — tam tanımlar v1.5 briefinde mevcuttur ve aynen geçerlidir.

### 4.1. Veri toplama katmanı

1. **Manuel Tag Ekleme** (v1.5 §4.1 ile aynı)
2. **Modbus Auto-Scan + Aktivasyon Akışı** (v1.5 §4.1 ile aynı)
2.5. **Polling Preset Sistemi** (v1.5 §4.1 ile aynı)
3. **Tag Browser** (v1.5 §4.1 ile aynı)

### 4.2. Asset sistemi

4. **Asset Template Library** — v1.5'teki 6 fabrika şablonu (Pompa, Chiller, Plate Heat Exchanger, Hava Kompresörü, Generic Motor, Generic Tank) korunur. **AVM template'leri F9'da eklenecektir** (§4.10).
5. **Binding Wizard** (v1.5 §4.2 ile aynı)
6. **Asset Instance Yönetimi** (v1.5 §4.2 ile aynı)

### 4.3. KPI ve analitik
7. **KPI Motoru** — Çekirdek aynı. AVM template'leri için KPI formülleri §4.10'da tanımlanacaktır (örn. chiller COP, AHU supply/return ΔT, cooling tower approach).
8. **KPI Sayfası** (v1.5 §4.3 ile aynı)
9. **ML Anomaly Detection** (v1.5 §4.3 ile aynı)

### 4.4. Alarm sistemi

10. **Threshold CRUD** (v1.5 §4.4 ile aynı)
11. **ISA-18.2 Alarm State Machine** (v1.5 §4.4 ile aynı)
12. **Alarm Sayfası** (v1.5 §4.4 ile aynı)

### 4.5. Bildirim

13. **Web Push Notifications** (v1.5 §4.5 ile aynı)
14. **Bildirim Ayarları Sayfası** (v1.5 §4.5 ile aynı)

### 4.6. Dashboard sayfaları

15–23. Overview, Sensors, Processes, KPI, Alarms, Logs, Settings, Maintenance, Assistant sayfaları — v1.5 §4.6 ile aynı.

### 4.7. Tasarım

24. **Visual Language** (v1.5 §4.7 ile aynı)

### 4.8. Bakım modülü (F8a — AVM'de de geçerli)

v1.5 §4.8 tamamen geçerlidir. Pilot değişikliğine göre iki not:
- **Checklist örnekleri AVM senaryolarıyla da çoğaltılacak** (örn. AHU fan arızası, cooling tower yüksek approach, booster set düşük basınç).
- **Periyodik takvim örnekleri AVM için:** AHU filtre kontrolü (aylık), cooling tower kimyasal dozaj kontrolü (haftalık), chiller kondenser temizliği (aylık veya sezonluk).

Teknik bileşenler (25, 26, 27) ve Maintenance Sayfası yapısı v1.5'teki ile birebir aynıdır.

### 4.9. Teknik asistan chatbot (F8b — AVM'de de geçerli)

v1.5 §4.9 tamamen geçerlidir. Pilot değişikliğine göre notlar:
- **Bilgi tabanı AVM'ye özel yazılacaktır.** Regin + Expo SCADA dokümanları ortak, ama ekipman dokümanları AVM tarafı (AHU, FCU, cooling tower, chiller uygulamaları AVM'de) için ayrıca hazırlanacaktır.
- **Doküman hedefi:** AVM pilot açılışı öncesi en az **10 makale** (sistem + ekipman + arıza + bakım kategorileri karışık).
- **Müşterinin beklentisi:** Chatbot esas olarak **teknik servis ekibinin otomasyon sistemini öğrenmesi** için isteniyor. Bu, dokümanların "prosedür + açıklama" ağırlıklı olmasını gerektirir (kısa Q&A değil).

Teknik bileşenler (28, 29, 30) — embedding modeli, FAISS, chat arayüzü — v1.5'teki ile birebir aynıdır.

### 4.10. AVM Asset Template Pack (F9 — YENİ, v1.6)

> **Durum: Template detayları bu brief yazılırken netleşmemiştir.** Saha araştırması ve müşteri teknik dokümantasyonu incelendikten sonra doldurulacaktır. Aşağıdaki liste bir iskelet ve başlangıç varsayımıdır.

**Hedef:** AVM pilotunda kullanılacak tipik ekipmanlar için asset template'leri ve KPI tanımları. Her template için: roller (required/optional), önerilen KPI formülleri, tipik alarm eşik değerleri (örnek — müşteri değiştirecek).

**İskelet template listesi (TBD — saha araştırması sonrası tamamlanacak):**

| Template | Beklenen roller (taslak) | Önerilen KPI'lar (taslak) |
|---|---|---|
| **AHU (Air Handling Unit)** | `supply_temp`, `return_temp`, `fan_current`, `filter_dp`, `damper_position`, `setpoint` | Supply/Return ΔT, fan güç tüketimi, filtre durumu |
| **Fan Coil Unit (FCU)** | `supply_temp`, `valve_position`, `fan_speed`, `room_temp` *(opsiyonel)* | Room ΔT, valve açıklık yüzdesi |
| **Cooling Tower** | `fan_current`, `sump_temp`, `water_level`, `outlet_temp`, `inlet_temp` | Approach temperature, range, fan verimlilik |
| **Booster Pump Set** | `suction_pressure`, `discharge_pressure`, `pump1_current`...`pumpN_current`, `flow_rate` *(opsiyonel)*, `vfd_hz` *(opsiyonel)* | Toplam debi, pompa çalışma saati dengesi, spesifik enerji |
| **Energy Meter** | `active_power`, `reactive_power`, `energy_kwh`, `power_factor`, `thd` *(opsiyonel)* | Tüketim profili, cos φ, puant yük |
| **Chiller** *(v1.5'ten korundu)* | `supply_temp`, `return_temp`, `compressor_current`, `refrigerant_pressure`, `ambient_temp` | COP, ΔT, basınç-sıcaklık mantığı |

**Template seçim kararları (alınacak — §4.10 güncellenecek):**
- Jeneratör izleme pilotta var mı, yok mu?
- Yangın pompası seti ayrı template mi, yoksa Booster Pump Set varyasyonu mu?
- Asansör, ekskalatör izleme **kapsam dışı** (alarm sistemleri zaten kendi SCADA'sında; scope creep'i kesiyoruz).
- Otopark havalandırma (CO/CO₂ sensörleri) ayrı AHU varyasyonu mu yoksa generic AHU ile çözülebilir mi?
- Isıtma sistemi (kazan, boyler) bu pilotta var mı?

**Dosya yapısı (önerilen):**
```
templates/
├── industrial/     ← v1.5 fabrika şablonları (mevcut)
│   ├── pump.yaml
│   ├── chiller.yaml
│   ├── plate_heat_exchanger.yaml
│   ├── air_compressor.yaml
│   ├── generic_motor.yaml
│   └── generic_tank.yaml
└── hvac/           ← v1.6 AVM şablonları (yeni — F9'da eklenecek)
    ├── ahu.yaml
    ├── fcu.yaml
    ├── cooling_tower.yaml
    ├── booster_pump_set.yaml
    └── energy_meter.yaml
```

### 4.11. AVM Deploy + Saha Hazırlığı (F10 — YENİ, v1.6)

**Hedef:** Regin + Expo SCADA kurulumu ile Custos kurulumunun saha üzerinde koordineli devreye alınması.

**İçerik:**
- **Connection Diagnostic Sayfası** (eğer F3'te hafif versiyonu yapıldıysa, AVM için zenginleştirme): Modbus slave health check, ping/cevap süresi, tag başarı oranı, son hata mesajı.
- **Setup Wizard scriptleri**: İlk açılışta admin şifresi, mDNS adı (`custos-avm.local`), zaman dilimi, SMTP bilgisi (opsiyonel).
- **Systemd service** düzeni, otomatik yeniden başlatma, logrotate.
- **Backup / export**: Database dump scripti (pilot sonrası veri geri taşıma için).
- **Kabul testi checklist'i**: 12 maddelik saha kabul testi (her template için en az 1 instance oluştur, 1 eşik tanımla, 1 alarm tetikle, 1 chatbot sorusu sor, 1 bakım checklist'i çalıştır, vb.).
- **Saha kurulum rehberi** (iç doküman): Regin ile eşleşme haritası, Modbus unit ID dağılımı, polling preset önerileri, bilinen tuzaklar.

---

## 5. Teknik mimari

### 5.1. Stack (kilitli — v1.5'ten değişmedi)

- **OS:** Ubuntu 24.04 LTS
- **Backend:** Python 3.12 + FastAPI + Uvicorn
- **Veritabanı:** PostgreSQL 16 + TimescaleDB
- **Modbus:** `pymodbus`
- **Template engine:** Jinja2
- **Frontend:** HTMX 2.0 + Alpine.js 3.14 + uPlot 1.6
- **CSS:** Tailwind 3.4 standalone binary
- **ML:** scikit-learn, numpy, pandas
- **Semantic Search:** sentence-transformers (paraphrase-multilingual-MiniLM-L12-v2), faiss-cpu
- **Background jobs:** APScheduler / asyncio task
- **Web Push:** `pywebpush`

### 5.2. İki süreçli mimari (v1.5'ten değişmedi)

- **`custos.critical`** — Collector + Threshold Engine + Alarm dispatcher
- **`custos.analytics`** — Feature engine + ML + KPI + Dashboard + Web Push + Bakım zamanlayıcı + Chatbot
- **`custos.shared`** — Config, logging, database abstract interface, domain models

### 5.3. Donanım (pilot için — v1.5'ten değişmedi)

- Intel N100/N200 fansız Mini PC, 16 GB RAM, 500 GB NVMe SSD, Gigabit Ethernet

### 5.4. Deployment (v1.5'ten değişmedi)

- Docker Compose + systemd, lokal ağ erişimi (`http://custos.local`)

### 5.5. Bilgi tabanı dosya yapısı (v1.5'ten değişmedi, AVM içeriği eklenecek)

v1.5 §5.5'teki yapı aynen geçerlidir. `data/knowledge/` altına AVM dokümanları (AHU, FCU, cooling tower, booster, energy meter) eklenir.

### 5.6. Çoklu kurulum (yeni — v1.6)

AVM ve fabrika **iki ayrı kurulum**tur. Multi-tenant yok.
- Her saha kendi mini PC'sinde çalışır, kendi veritabanına yazar, kendi bilgi tabanını tutar.
- Kod tabanı tek, config/data farklı. Docker Compose `.env` ile farklılaşır.
- Uzak destek için VPN bazlı erişim; her iki sahanın VPN endpoint'i ayrı.

---

## 6. Aşama 4 feature sıralaması (revize)

**Başlangıç:** F1-F7 tamamlandı (v1.5'teki plan).
**Bugün:** 16 Nisan 2026 (Perşembe).
**AVM pilot tarihi:** 5 Haziran 2026 (Cuma). Kalan süre: yaklaşık 7 hafta 1 gün.

| Hafta | Tarih aralığı | Feature | Kapsam |
|---|---|---|---|
| **W1** | 16–22 Nisan | **F8a: Bakım Modülü** | Maintenance DB şeması, periyodik takvimler, checklist CRUD, alarm-checklist eşleme, Maintenance sayfası. |
| **W2** | 23–29 Nisan | **F8b: Teknik Asistan Chatbot** | Bilgi tabanı indeksleme, sentence-transformers + FAISS, semantic search API, chat arayüzü, Custos veri entegrasyonu. |
| **W3** | 30 Nisan – 6 Mayıs | **F9: AVM Template Pack** | AHU, FCU, Cooling Tower, Booster, Energy Meter template'leri; KPI formülleri; örnek threshold ve checklist'ler; AVM bilgi tabanı dokümanları (ilk 10 makale). |
| **W4** | 7–13 Mayıs | **F10: AVM Deploy + Saha Hazırlığı** | Setup wizard, Connection Diagnostic, systemd, backup script, kabul testi checklist'i, saha kurulum rehberi. |
| **W5** | 14–20 Mayıs | **Saha entegrasyonu 1 (Regin + Modbus map)** | Regin PLC programlama ve Modbus register map'inin Custos ile hizalanması (ortak ile). |
| **W6** | 21–27 Mayıs | **Saha entegrasyonu 2 (tuning + şablon bağlama)** | Tüm ekipmanlar için tag → role bağlama, eşik ayarı, KPI doğrulama, chatbot soru-cevap provası. |
| **W7** | 28 Mayıs – 4 Haziran | **Son test + buffer** | Kabul testi, yedekli senaryolar, müşteri eğitim oturumu, buffer (ortalama geciken 1-2 iş için). |
| **5 Haziran** | — | **AVM Pilot Go-Live** | — |

**Fabrika pilotu:** AVM go-live sonrası 2 hafta "kapı arkası" değerlendirme; Temmuz ortası fabrika entegrasyonu başlar. Kesin tarih AVM pilotunun nasıl gittiğine göre belirlenir.

**Kritik uyarı:** Bu takvim çok dardır. Herhangi bir feature 3+ gün gecikirse kapsam kesme kaçınılmazdır. Kesme önceliği (feda edilecek sıra):
1. F9'daki "ikinci öncelik template'ler" (örn. Energy Meter gelişmiş KPI'lar)
2. Chatbot'un Custos veri entegrasyonu (sadece doküman arama ile açılabilir)
3. Bakım modülünde periyodik takvim (sadece alarm-checklist ile açılabilir)
4. Asla kesilmez: Collector, Threshold, Alarm state machine, Binding Wizard, Overview + Sensors + Processes + Alarms sayfaları.

Her hafta sonunda: tüm testler yeşil, ruff + mypy temiz, commit mesajları Türkçe, git log düzgün, demo-able durum.

---

## 7. Çalışma kuralları

v1.5 §7'deki 10 kural aynen geçerlidir (tekrar yazılmıyor). Altı çizili nokta:

- **Kural 9 "Brief değişirse versiyon artar."** Bu versiyon (v1.6) AVM teklifi nedeniyle yazılı kararla açılmıştır.
- **Kural 10 "Minimum hareketli parça."** AVM pilotu 7 haftada teslim edilecek; yeni bağımlılık eklenmeyecek, yeni "nice to have" kabul edilmeyecek.

Ek olarak v1.6'ya özel:

11. **Scope kesme önceliği belgelenmiştir** (bkz. §6 kritik uyarı). Gecikme halinde kesme kararı 24 saat içinde alınır, yazılı olarak brief'e eklenir.
12. **AVM pilotu ücretli pilottur.** Başarısızlık maliyeti daha yüksektir. Her feature kapanışında "müşteri bunu 5 Haziran'da gerçek kullanımla deneyecek, güveniyor muyum?" sorusu sorulur.

---

## 8. Bilinen riskler ve azaltımları

v1.5'teki riskler korundu + AVM/süre baskısıyla ilgili yeniler eklendi.

| Risk | Olasılık | Etki | Azaltım |
|---|---|---|---|
| **7 haftalık takvim sürtünmeyle kayar** (yeni) | Yüksek | Çok Yüksek | §6'daki kesme önceliği uygulanır; haftalık check-in; F9 ve F10 paralel ilerleyebilir |
| **AVM 7/24 çalışıyor, kurulum penceresi dar** (yeni) | Yüksek | Orta | Gece veya düşük yoğunluk saatlerinde kurulum; laboratuvarda ön hazırlık (Modbus simülatörle prova); saha günü "plug-in" kadar kısa |
| **Regin PLC map'i gecikir, Modbus register ready değilken Custos'a bağlanamayız** (yeni) | Orta-Yüksek | Yüksek | Ortak ile haftalık sync; register map taslağı W3 sonunda donar, W5'te fiziksel bağlantı |
| **AVM bilgi tabanı dokümanları zamanında yazılmaz** (yeni) | Orta-Yüksek | Orta | Minimum 10 makale W3'te yazılır; pilot sonrası artırılır; chatbot boş tabanla da çalışır ("bu konuda bilgi bulamadım") |
| **Ücretli pilot — müşteri memnun kalmazsa ticari risk** (yeni) | Orta | Yüksek | Haftalık müşteri check-in; erken demo (W5 sonu); beklenti yönetimi yazılı |
| Modbus Auto-Scan yetersiz kalır | Düşük | Orta | AVM'de map'i biz tanımladığımız için risk düşük; fabrikada orta; manuel tag ekleme fallback var |
| Eski PLC fast polling'i kaldıramaz | Düşük (AVM) | Orta | AVM'de Regin yeni, kapasite biliniyor; Fast Polling Budget = 10 tag |
| Pilot kurulum günü Modbus bağlantı sorunları | Orta | Yüksek | Uzaktan ön test (VPN); Connection Diagnostic sayfası F10'da; kurulum öncesi full dry-run |
| ML modeli ilk gün anlamlı sonuç üretmez | Yüksek | Düşük | Açıkça "öğrenme süresi gerekir" iletişimi; ilk hafta eşik alarmı öne çıkar |
| Mini PC endüstriyel/ticari ortamda ısınma | Düşük-Orta | Orta | Fansız N100/N200; AVM IT odasında olabilir — ortam daha kontrollü; pilot sonrası enclosure düşünülecek |
| Müşteri pilot sırasında yeni feature ister | Yüksek | Orta | v1.1 backlog açık; her istek yazılı; MVP'ye girmez (v1.6'da daha katı) |
| Tek kişilik geliştirme tempo riski | Yüksek | Yüksek | Sabah/akşam kısa check-in; ortak ile saha işi paralel; yorgunluk sinyallerinde zorunlu mola |
| Sentence-transformers Türkçe kalitesi | Düşük-Orta | Orta | Multilingual model; YAML Q&A exact-match fallback; W2 sonunda prova |
| Embedding modeli N100'de yavaş | Düşük | Düşük | Model bir kez yüklenir; ~50-100 ms arama; startup 30-60 s kabul |

---

## 9. Pilot sonrası (v1.1+) backlog

v1.5 §9'daki liste geçerlidir. AVM pilotundan çıkma olasılığı yüksek yeni backlog adayları:

- Multi-site / multi-tenant (AVM + fabrika aynı dashboard'dan izlensin talebi gelebilir)
- BACnet/IP desteği (AVM otomasyon dünyasında yaygın)
- Kiracı (tenant) bazlı enerji raporlaması
- Vardiya bazlı alarm yönlendirme (AVM'de gece/gündüz teknisyen farklı olabilir)
- Mobil bildirim (PWA veya native app)
- Chatbot'ta çok turlu diyalog (pilot sonrası feedback'e göre)
- Bakım geçmişi raporları (PDF export)
- Enerji verimliliği raporları (AVM için kritik)

---

## 10. Sıradaki adım

**Hemen yapılacak:**
1. Bu briefin (v1.6) kullanıcı tarafından onaylanması.
2. **F8a: Bakım Modülü** feature prompt'u (`claude_code_prompt_12_f8a_maintenance.md`) — W1 başlangıcı.
3. AVM template araştırması (paralel): müşteri ile teknik toplantı, ekipman listesi + approx tag sayısı + Regin map taslağı. §4.10 güncellenecek.
4. AVM bilgi tabanı doküman iskeletinin yazılması (paralel, ortak ile).

**F9 öncesi netleşmesi gerekenler (W3 başlamadan):**
- AVM ekipman kesin listesi
- Her template için rol listesi ve kardinalite
- Enerji sayacı tipi ve Modbus haritası (üretici bilgisi)
- Jeneratör, yangın pompası dahil mi?
- Otopark havalandırma dahil mi?

**AVM pilot öncesi netleşmesi gerekenler (W5 başlamadan):**
- Kurulum tarihi ve saha erişim saatleri
- VPN + uzaktan destek modeli
- Müşteri tarafı iletişim kişisi ve eğitim planı
- Pilot kabul testi oturumu tarihi

---

**Bu doküman yeni AVM teklifi ile birlikte güncellenmiştir. v1.5'teki fabrika brief'i ikincil pilot olarak korunmuştur. Değişmesi gerekirse versiyon artırılarak revize edilir. Sessizce düzenleme yapılmaz.**
