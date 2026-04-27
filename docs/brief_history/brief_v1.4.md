# Custos — Proje Brief

**Versiyon:** 1.4
**Tarih:** 11 Nisan 2026
**Durum:** Tag aktivasyon akışı, polling preset sistemi ve template-bağlama opsiyonelliği netleştirildi
**Önceki versiyonlar:** v1.0 (8 Nisan), v1.1, v1.2 (Custos adı), v1.3 (10 Nisan, pilot müşteri)

---

## 0. Bu versiyonda ne değişti

v1.3 → v1.4 değişiklikleri (yapısal değil, netleştirme):

- **Tag yaşam döngüsü** netleştirildi: `discovered` → `active` / `ignored` durumları, sözlüğe eklendi.
- **Polling preset sistemi** tanımlandı: Slow (10 s, default) / Normal (1 s) / Fast (0.1 s = 10 Hz). Tag başına ayarlanır, sonradan güncellenebilir.
- **Fast polling budget**: Pilot için maksimum **10 tag** fast polling sınırı (aşılırsa kullanıcı uyarılır).
- **Slave latency probing**: Connection profile kaydedildikten sonra her slave için cevap süresi ölçülür ve kullanıcıya gösterilir.
- **Tag aktivasyon akışı sürtünmesizleştirildi**: Scan → tablo → çoklu seç → tek tık aktifleştir. Aktivasyonda isim ve polling sorulmaz; default isim `Tag_40001` formatında, default polling 10 saniye. Kullanıcı sonradan Sensors sayfasında günceller.
- **Sensors sayfası tag yönetim merkezi**: Aktivasyon sonrası kullanıcı buraya yönlendirilir; isim/polling/eşik düzenlemeleri burada yapılır.
- **Template bağlama opsiyonel**: Tag bir asset template'e bağlı olmadan da grafik, eşik alarm ve genel ML anomali tespitinden yararlanabilir. Template bağlama yalnızca KPI hesabı ve proses sayfası görünümü için gereklidir. Bu, ürünün iki modlu kullanımını mümkün kılar:
  - **Hızlı mod**: tag aktif et + eşik koy + alarm al
  - **Zengin mod**: template'e bağla + KPI + per-asset ML

v1.2 → v1.3 arasındaki ana değişiklik (tarihsel kayıt): **proje artık gerçek bir pilot müşteriye sahiptir.** Bir üretim fabrikası ile yapılan ön görüşmede, fabrikanın teknik ekibi "neye ihtiyacınız var" sorusuna cevaben bir gereksinim listesi vermiştir. Bu liste Custos'un v1 MVP kapsamını belirlemektedir.

Önceki brief'lerdeki "şablon = sadece görsel", "LLM yok", "bildirim = Telegram" kararları müşteri gereksinimleri doğrultusunda revize edilmiştir:

- Asset Template sistemi artık **işlevsel** — şablonlar KPI hesaplamasını otomatik yapar.
- Bildirim kanalı olarak **Web Push** seçilmiştir (SMS pilot sonrasına ertelendi).
- Modbus **Auto-Scan** MVP'ye eklenmiştir.
- Terminoloji, endüstri standartlarına (OPC UA, SCADA, ISA-18.2) hizalanmıştır.

Mimari kararlar (iki süreçli yapı, critical loop / analytics loop ayrımı, HTMX + Alpine + uPlot + Jinja2 stack, koyu tema Grafana ailesi tasarım dili) **değişmemiştir**.

---

## 1. Ürün tanımı

Custos, üretim tesislerinde Modbus TCP üzerinden sensör verisi okuyan, bu verileri endüstri standardı asset şablonlarına bağlayan, KPI hesaplayan, ML tabanlı anomali tespiti ve eşik alarmı üreten, lokal çalışan bir edge izleme sistemidir.

**Temel kural değişmedi:** Sistem **sadece okur, asla yazmaz.** Prosese müdahale yoktur. Amaç: cihaz ve işletme koruması, erken uyarı, operasyonel görünürlük.

**Pilot müşteri için tek cümlelik değer önerisi:** *"Fabrikanızın verilerini okuyan, sizin anladığınız dilde (pompa, chiller, kompresör) özetleyen, problem olmadan önce size haber veren lokal bir akıllı göz."*

---

## 2. Pilot müşteri

**Profil:**
- Sektör: Üretim fabrikası
- Haberleşme: Modbus TCP (mevcut)
- Sensör çeşitliliği: Yüksek (sıcaklık, basınç, akış, seviye, motor akımı/frekans, muhtemelen titreşim ve enerji sayaçları)
- İhtiyaç toplama yöntemi: Needs discovery — müşteri kendi ağzından söyledi, geliştirici önermedi
- Teslim tarihi: **8 Haziran 2026 civarı (10 Nisan + ~60 gün)**

**Pilot başarı kriteri:** Fabrikada kurulum gününden itibaren sistem kesintisiz çalışır, teknik ekip dashboard'u günlük kullanır, en az bir gerçek uyarı üretir (eşik veya anomali), en az bir asset instance (örneğin bir pompa veya chiller) şablonla bağlanmış haldedir.

---

## 3. Domain sözlüğü (Glossary)

Kod, template, dokümantasyon ve arayüzde aşağıdaki terimler kullanılacaktır. Türkçe gündelik karşılıkları yerine standart endüstriyel terimler tercih edilir, çünkü hedef müşteri SCADA/otomasyon dünyasından gelir ve bu terimleri tanır.

| Terim | Türkçe karşılığı | Kod isimlendirmesi |
|---|---|---|
| **Tag** | Sensör / ham veri noktası | `tag`, `tag_id` |
| **Tag Reading** | Tag'in tek bir okunması | `tag_reading` |
| **Asset Template** | Proses şablonu | `asset_template` |
| **Asset Instance** | Somut proses (örn. "Sirkülasyon Pompası #1") | `asset_instance` |
| **Template Role** | Şablonun beklediği tag rolü (örn. `suction_pressure`) | `role`, `role_key` |
| **Tag Binding** | Bir tag'in bir asset rolüne atanması | `tag_binding` |
| **Scaling** | `real = raw * gain + offset` dönüşümü | `gain`, `offset` |
| **Threshold** | Alarm eşiği | `threshold` |
| **Alarm State Machine** | ISA-18.2 alarm yaşam döngüsü | `alarm_state` (`normal`/`triggered`/`acknowledged`/`cleared`) |
| **Debouncing** | Eşik aşımının X saniye sürmesi şartı | `debounce_seconds` |
| **Hysteresis** | Alarm temizleme için ölü bant | `hysteresis` |
| **KPI Definition** | Şablon bazlı hesaplama tanımı | `kpi_definition` |
| **Computed Tag** | Diğer tag'lerden türetilen tag | `computed_tag` |
| **Anomaly Detection** | ML tabanlı sapma tespiti | `anomaly_detector` |
| **Tag Status** | Tag yaşam döngüsü durumu | `tag_status` (`discovered` / `active` / `ignored`) |
| **Polling Interval** | Tag'in okunma sıklığı (ms) | `polling_interval_ms` (default 10000) |
| **Polling Preset** | Hazır okuma hız seviyesi | `polling_preset` (`slow` / `normal` / `fast` / `custom`) |
| **Slave Latency** | Modbus slave cevap süresi | `slave_latency_ms` (min/avg/max) |
| **Fast Polling Budget** | Sistem genelinde fast tag sınırı | Pilot için maksimum 10 |

Bu sözlük Aşama 4'ün tüm kod isimlendirmesi için kilittir. Aşama 3'te yazılmış olan `raw_readings` tablosu `tag_readings` olarak, `sensor_id` alanları `tag_id` olarak **ilk migration ile yeniden adlandırılacaktır** (bu iş Aşama 4 Feature 1'e eklenecek).

---

## 4. MVP Feature Listesi

Aşağıdaki özelliklerin tamamı pilot müşteri tarafından gereksinim olarak belirtilmiştir. Hiçbiri kesilmemiştir. Pilot sonrası (v1.1+) genişlemeler bu listenin dışındadır.

### 4.1. Veri toplama katmanı

1. **Manuel Tag Ekleme** — Kullanıcı Modbus adresi, veri tipi, byte order, scaling, birim girerek tek tek tag ekleyebilir. Auto-scan çalışmadığı veya kullanıcının kontrol istediği durumlar için güvenli fallback.

2. **Modbus Auto-Scan + Aktivasyon Akışı** — Kullanıcı bir Modbus TCP connection profile girer (host + port + unit ID aralığı). Sistem sırasıyla:
   - **Slave scan**: hangi unit ID'ler cevap veriyor
   - **Slave latency probing**: her slave için 10-20 test okuma yapılır, min/avg/max cevap süresi kaydedilir. Bu değer fast polling kararlarında referans olur.
   - **Register map discovery**: her slave'de hangi register'lar geçerli veri döndürüyor (function code 0x03 ve 0x04)
   - **Register type inference**: ham değerlerden muhtemel veri tipini tahmin eder (uint16/int16/float32 + byte order)
   - Keşfedilen her register için **aday tag** oluşturur (`status = 'discovered'`), tabloda gösterir.

   **Tablo sütunları**: Checkbox, Adres, Veri Tipi (tahmin), Son Değer, Min/Max (scan süresince), Önerilen Birim, Notlar (geçersiz/stabil/gürültülü).

   **Filtre araçları**: Arama, "sadece stabil olanları göster" toggle, "Tümünü Seç" başlık checkbox'ı.

   **Toplu aktivasyon**: Kullanıcı checkbox'larla seçim yapar, alttaki çubuktan iki aksiyon seçebilir:
   - **"Seçilenleri Onayla ve Aktifleştir"**: Tek tık. Polling preset, isim veya başka bir alan **sorulmaz**. Tüm seçilen tag'ler şu defaultlarla aktive edilir:
     - `status = 'active'`
     - `name = "Tag_<adres>"` (örn. `Tag_40001`)
     - `polling_interval_ms = 10000` (Slow preset)
   - **"Seçilenleri Yok Say (Ignore)"**: Tek tık. Tag'ler `status = 'ignored'` olur, listeden gizlenir, **bir sonraki scan'de tekrar aday olarak gelmezler**. Sensors sayfasındaki "Ignored" sekmesinden geri çağrılabilir.

   Aktivasyon sonrası kullanıcı **otomatik olarak Sensors sayfasına yönlendirilir**. Tüm sonraki düzenlemeler (isim, polling interval, eşik, vs.) Sensors sayfasında yapılır.

   **Sınır**: Auto-scan kusursuz olmayacaktır. Kullanıcı her aday tag'i manuel olarak doğrulamak, isim vermek ve gerekirse reddetmek zorundadır. Bu bir **asistan özelliği**, otomatik sihir değil.

2.5. **Polling Preset Sistemi** — Her aktif tag'in `polling_interval_ms` değeri vardır. Kullanıcı tag'i Sensors sayfasında düzenleyerek hızını değiştirir. Üç preset + custom:
   - **Slow** = 10 saniye (default — sıcaklık, seviye, yavaş değişenler)
   - **Normal** = 1 saniye (basınç, akış, motor akımı)
   - **Fast** = 100 ms = 10 Hz (titreşim, hızlı spike yakalama)
   - **Custom**: kullanıcı serbest değer girer

   **Fast Polling Budget**: Pilot için sistem genelinde maksimum **10 tag** fast (≤1 saniye) polling'e alınabilir. 11. tag fast yapılmak istenirse uyarı çıkar. Bu sınır Settings'ten override edilebilir ama varsayılan koruma budur. Sebep: mini PC kaynak budget'ı + Modbus slave cevap süresi limiti.

   **Slave Latency Uyarısı**: Kullanıcı bir tag için seçtiği polling interval, o slave'in ölçülmüş latency'sinden daha kısaysa sistem uyarır ("Bu slave ortalama 45 ms cevap veriyor, 100 ms polling güvenli, 50 ms riskli").

3. **Tag Browser** — Auto-scan veya manuel olarak eklenen tüm tag'lerin listesi. Her tag için: ad, adres, birim, son değer, son güncelleme zamanı, trend mini-grafik. Filtreleme ve arama.

### 4.2. Asset sistemi

> **Önemli:** Template'e tag bağlamak **opsiyoneldir**. Bir tag template'e bağlı olmadan da grafik, eşik alarm ve genel ML anomali tespitinden yararlanır. Template bağlama yalnızca **KPI hesabı** ve **Processes sayfasındaki proses görünümü** için gereklidir. Bu, ürünün iki modlu kullanımını mümkın kılar:
> - **Hızlı mod** — kullanıcı tag aktif eder, eşik koyar, alarm alır, grafik izler. Template'e hiç dokunmaz. Pilot'un ilk gününde bu mod yeterli faydadır.
> - **Zengin mod** — kullanıcı tag'leri asset template'lere bağlar, Processes sayfasını kullanır, KPI hesaplarını ve per-asset ML'i devreye alır. Pilot'un ilerleyen günlerinde aktive edilir.

4. **Asset Template Library** — Hazır şablon kütüphanesi. Pilot için minimum 5-6 şablon:
   - Pompa (roller: suction_pressure, discharge_pressure, motor_current, flow_rate, winding_temperature)
   - Chiller (roller: supply_temp, return_temp, compressor_current, refrigerant_pressure, ambient_temp)
   - Plate Heat Exchanger (roller: hot_in, hot_out, cold_in, cold_out, flow_rate)
   - Hava Kompresörü (roller: discharge_pressure, motor_current, oil_temp, ambient_temp)
   - Generic Motor (roller: current, voltage, winding_temp, vibration)
   - Generic Tank (roller: level, temperature, pressure)

   Her şablonda: rol listesi (required / optional flag ile), önerilen KPI tanımları, önerilen alarm kuralları (örnek değerler — kullanıcı değiştirecek).

5. **Binding Wizard** — Adım adım akış: Template seç → Asset instance'a isim ver ("Sirkülasyon Pompası #1") → her rol için tag seç → tag'in son değerini **live preview** olarak gör ("evet bu basınç") → kaydet. Live preview kritik — kullanıcı yanlış tag'i role bağlamasını engeller.

6. **Asset Instance Yönetimi** — Oluşturulan asset'ların listesi, düzenleme, silme, ikonla görselleştirme.

### 4.3. KPI ve analitik

7. **KPI Motoru** — Şablon başına tanımlı KPI formülleri. Bir asset instance oluşturulduğu anda, bağlı tag'ler üzerinden KPI'ları otomatik hesaplar. Örnek: Pompa için `specific_energy = motor_current * voltage / flow_rate`. Hesaplama aralığı: 1 dakikalık bucket'lar.

8. **KPI Sayfası** — Tüm aktif KPI'ların listesi ve trend grafikleri. Asset'a göre filtreleme.

9. **ML Anomaly Detection** — Basit başlangıç: Isolation Forest, asset instance başına bir model, günlük retrain (arka plan job). Sapma skorlarının dashboard'da görünümü. Gelişmiş yöntemler v1.1+.

### 4.4. Alarm sistemi

10. **Threshold CRUD** — Her tag veya KPI için min/max alarm eşiği tanımlama, debounce süresi, hysteresis değeri. Dashboard üzerinden tamamen düzenlenebilir.

11. **ISA-18.2 Alarm State Machine** — `normal` → `triggered` → `acknowledged` → `cleared` durumları. Acknowledge kullanıcı etkileşimiyle olur, clear otomatik (değer eşik içine dönünce + hysteresis süresi geçince).

12. **Alarm Sayfası** — Aktif alarmlar (durum rengiyle), alarm geçmişi, filtreleme, acknowledge butonu. Her alarm satırında: tag/asset, zaman, tetikleyen değer, durum.

### 4.5. Bildirim

13. **Web Push Notifications** — Tarayıcı üzerinden bildirim. VAPID key üretimi, Service Worker, abonelik yönetimi. Kullanıcı dashboard'a giriyorsa masaüstü/mobil bildirim alır. Harici bağımlılık yok, ek maliyet yok.

14. **Bildirim Ayarları Sayfası** — Hangi alarm seviyesi bildirim tetikler, sessiz saatler, kullanıcı başına abonelik. Settings sayfasının parçası.

**Not:** SMS bildirimi pilot sonrasına ertelendi. Karar gerekçesi: SMS gateway entegrasyonu (hesap açma, test, rate limit) bir haftayı yiyebilir; Web Push sıfır harici bağımlılık ile çalışır. Pilot sonrası gerçek kullanıcı geri bildirimiyle SMS/Telegram/e-posta kanalı seçilecektir.

### 4.6. Dashboard sayfaları

15. **Overview Sayfası** — Ana sayfa. KPI özet kutuları (aktif alarm sayısı, toplam asset, toplam tag, uptime), hero trend grafikleri, son alarm tablosu.

16. **Sensors (Tags) Sayfası** — Modbus'tan okunan tüm tag'lerin yönetim sayfası. Auto-scan başlatma, manuel ekleme, düzenleme.

17. **Processes (Assets) Sayfası** — Asset instance'ların listesi ve detay sayfaları. Her asset detayında: bağlı tag'lerin canlı değerleri, KPI'lar, trend grafikleri, alarm geçmişi.

18. **KPI Sayfası** — Tüm KPI'lar, trendler, filtreler.

19. **Alarms Sayfası** — Aktif + geçmiş alarmlar, state machine yönetimi.

20. **Logs Sayfası** — Sistem event'leri: Modbus bağlantı durumu, scan sonuçları, ML model retrain olayları, kullanıcı aksiyonları (audit trail).

21. **Settings Sayfası** — Modbus connection profile'ları, bildirim ayarları, kullanıcı bilgileri.

### 4.7. Tasarım

22. **Visual Language** — Grafana ailesi, koyu tema, sabit sol sidebar + üst header + içerik grid. Component kütüphanesi: KPI card, chart panel (uPlot), status badge, data table, form elements. Referans ekran: Unraid Grafana dashboard stili.

---

## 5. Teknik mimari

### 5.1. Stack (kilitli)

- **OS:** Ubuntu 24.04 LTS (mini PC)
- **Backend:** Python 3.12 + FastAPI + Uvicorn
- **Veritabanı:** PostgreSQL 16 + TimescaleDB extension
- **Modbus:** `pymodbus` kütüphanesi
- **Template engine:** Jinja2
- **Frontend (server-rendered):** HTMX 2.0 + Alpine.js 3.14 + uPlot 1.6
- **CSS:** Tailwind CSS 3.4 standalone binary (Node.js yok)
- **ML:** scikit-learn (Isolation Forest), numpy, pandas
- **Background jobs:** APScheduler veya basit asyncio task'ları
- **Web Push:** `pywebpush` kütüphanesi

### 5.2. İki süreçli mimari (korunuyor)

- **`custos.critical`** — Düşük latency, kritik yol. Collector (Modbus polling) + Threshold Engine + Alarm dispatcher.
- **`custos.analytics`** — Daha yüksek latency tolere eder. Feature engine + ML + KPI motor + Dashboard + Web Push.
- **`custos.shared`** — Config, logging, database abstract interface, domain models.

### 5.3. Donanım (pilot için)

- Intel N100 veya N200 sınıfı **fansız Mini PC**
- **16 GB RAM**
- **500 GB NVMe SSD**
- Gigabit Ethernet (fabrika LAN'ına Modbus TCP için)
- Marka seçimi pilot öncesine bırakıldı (Beelink / GMKTec / Topton / MINIX aday)

### 5.4. Deployment

- Docker Compose (mevcut docker-compose.yml genişletilecek)
- Systemd service (mini PC boot'ta otomatik başlasın)
- Lokal ağda erişim: `http://custos.local` (mDNS) veya statik IP

---

## 6. Aşama 4 feature sıralaması (8 haftalık plan)

Her feature 5-6 günlük bir sprint olarak planlanmıştır. Hafta 8'in son 2 günü **kesinlikle buffer** — gerçek kurulum öncesi son test ve fix. Feature'lar bağımlılık sırasına göre dizilmiştir: her feature'ın bir öncekine ihtiyacı vardır.

| Hafta | Feature | Kapsam |
|---|---|---|
| **1-2** | **F1: Visual Language + Dashboard Shell** | Tasarım dili, component kütüphanesi, layout shell, referans overview sayfası (sahte data). Prompt 3 zaten hazır. |
| **3** | **F2: Tag Modeli + Manuel CRUD + Sensors Sayfası** | `tag`, `tag_reading`, `tag_binding` şemaları. Aşama 3'teki `raw_readings` → `tag_readings` migration. Manuel tag CRUD. Sensors sayfası. |
| **4** | **F3: Modbus Auto-Scan + Tag Browser** | Connection profile yönetimi, slave scan, register discovery, type inference, aday tag onay akışı. |
| **5** | **F4: Asset Template + Binding Wizard + Processes Sayfası** | Template library seed'i, Asset instance CRUD, Binding Wizard (live preview dahil), Processes sayfası. |
| **6** | **F5: Threshold Engine + Alarm Sayfası + Logs** | ISA-18.2 state machine, threshold CRUD, alarm event'leri, Alarm sayfası, Logs sayfası (audit trail). |
| **7** | **F6: KPI Motoru + KPI Sayfası + ML Anomaly** | KPI formül engine, asset başına Isolation Forest, günlük retrain job, KPI sayfası. |
| **8** | **F7: Web Push + Settings + Pilot Hazırlık** | VAPID keys, Service Worker, subscription, Settings sayfası, Mini PC setup script'i, systemd service, pilot kurulum rehberi. Son 2 gün buffer. |

Her hafta sonunda: tüm testler yeşil, ruff + mypy temiz, commit mesajları Türkçe, git log düzgün, demo-able durum. Hafta sonları buffer olarak kullanılabilir ama plana dahil değildir.

---

## 7. Çalışma kuralları

Aşağıdaki kurallar Aşama 4 boyunca uygulanacaktır. Bunlar OrientPro'dan alınan derslerden ve Aşama 1-3'te doğruluğu kanıtlanmış pratiklerden gelir.

1. **Strateji Claude.ai, inşa Claude Code.** Bu brief ve feature prompt'ları Claude.ai'da yazılır, Claude Code sadece prompt'u alır ve uygular.
2. **Her feature kendi prompt dosyasıyla gelir.** `claude_code_prompt_N_feature_adi.md` formatı korunur. Prompt taze oturumda başlar, CLAUDE.md ve brief'i okur, plan sunar, onay alır, uygular, mezuniyet fotoğrafıyla kapanır.
3. **Plan → onay → implementasyon → doğrulama → commit.** Bu döngü kırılmaz. Plan atlanırsa sürprizler gelir.
4. **Her feature için "var olacak" ve "olmayacak" listesi.** Scope creep'i feature seviyesinde keser.
5. **Test disiplini:** Backend için pytest, her feature en az route-level test yazar. Frontend için visual verification (tarayıcıda göz kontrolü + ekran görüntüsü).
6. **Commit mesajları Türkçe.** CLAUDE.md kuralı.
7. **Tüm datetime UTC.** CLAUDE.md kuralı.
8. **Node.js yok, CDN yok, harici font yok.** Lokal-first prensibi.
9. **Brief değişirse versiyon artar.** v1.3 → v1.4 ancak yazılı karar ile.
10. **Minimum hareketli parça.** Her yeni bağımlılık gerekçelidir.

---

## 8. Bilinen riskler ve azaltımları

| Risk | Olasılık | Etki | Azaltım |
|---|---|---|---|
| Modbus Auto-Scan sensör çeşitliliğinde yetersiz kalır | Orta-Yüksek | Orta | Manuel Tag ekleme fallback'i MVP'de var; auto-scan asistan olarak konumlanır, otomasyon değil |
| Eski PLC fast polling'i (>1 Hz) kaldıramaz, timeout üretir | Orta | Orta | Slave latency probing connection profile kaydında otomatik çalışır; kullanıcı seçtiği polling interval slave latency'sinden kısaysa uyarılır; fast polling budget pilot için 10 tag ile sınırlı |
| 8 haftalık plan sürtünmeyle kayar | Yüksek | Yüksek | Hafta 8'in son 2 günü buffer; haftalık check-in'lerde kayma tespit edilirse scope feature içinde kesilir, ertelenmez |
| Pilot kurulum günü Modbus bağlantı sorunları | Orta | Yüksek | Kurulum öncesi uzaktan test (VPN üzerinden), fabrika ağ bilgisi önceden toplama, connection diagnostic sayfası MVP'de var |
| ML modeli ilk gün anlamlı sonuç üretmez | Yüksek | Düşük | Açıkça "öğrenme süresi gerekir" olarak iletişim; ilk hafta eşik alarmı öne çıkar, ML ikinci planda |
| Mini PC endüstriyel ortamda ısınma/toz sorunu | Düşük-Orta | Orta | Fansız N100/N200 seçimi; pilot sonrası IP-rated enclosure düşünülecek |
| Müşteri pilot sırasında yeni feature ister | Yüksek | Orta | v1.1 backlog dosyası açık tutulur, her istek yazılı kayda girer, hiçbiri MVP'ye alınmaz |
| Tek kişilik geliştirme tempo riski | Yüksek | Yüksek | Günlük kısa check-in (sabah 15 dk plan, akşam 15 dk review), uyku ve mola disiplini, yorgunluk sinyallerinde mola |

---

## 9. Pilot sonrası (v1.1+) backlog

Bu özellikler MVP'ye dahil değildir ama pilot başarılı olursa ilk genişleme bu listeden gelir:

- SMS bildirim (Netgsm veya Twilio)
- Telegram bildirim
- E-posta bildirim
- Gelişmiş ML (EWMA, SPC kontrol kartları, multivariate anomaly)
- Template versioning
- Computed tags (formül tabanlı türev tag'ler)
- Multi-site / multi-tenant
- Mobil uygulama
- Rapor dışa aktarımı (PDF, Excel)
- Shift/vardiya bazlı KPI
- Historian replay
- Kullanıcı rolleri ve yetkilendirme
- Modbus RTU desteği
- OPC UA desteği
- Cloud sync opsiyonu

---

## 10. Sıradaki adım

Aşama 4 Feature 1: **Visual Language + Dashboard Shell**. Prompt 3 hazır (`claude_code_prompt_3_visual_language.md`). Brief v1.3 repo'ya `docs/brief_v1.3.md` olarak eklendikten sonra Claude Code oturumu başlatılır.

---

**Bu doküman pilot müşteri gereksinimleriyle doğrulanmıştır. Değişmesi gerekirse versiyon artırılarak revize edilir. Sessizce düzenleme yapılmaz.**
