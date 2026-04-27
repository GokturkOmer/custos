# Custos — Proje Brief

**Versiyon:** 1.5
**Tarih:** 14 Nisan 2026
**Durum:** Bakım modülü ve teknik asistan chatbot MVP kapsamına eklendi
**Önceki versiyonlar:** v1.0 (8 Nisan), v1.1, v1.2 (Custos adı), v1.3 (10 Nisan, pilot müşteri), v1.4 (11 Nisan, tag akışı netleştirme)

---

## 0. Bu versiyonda ne değişti

v1.4 → v1.5 değişiklikleri:

- **Bakım Modülü MVP'ye eklendi** — Pilot müşterinin ikinci görüşmesinde talep edildi. Periyodik bakım takvimleri, arıza bazlı kontrol listeleri (checklist) ve alarm tetiklemeli bakım önerileri.
- **Teknik Asistan Chatbot MVP'ye eklendi** — Aynı görüşmede talep edildi. Otomasyon sisteminin (Regin + Expo SCADA) çalışma prensipleri, arıza prosedürleri ve bakım talimatlarını içeren yerel bilgi tabanı üzerinde semantic search tabanlı soru-cevap botu. LLM kullanılmaz.
- **Yeni feature (F8)** eklendi: Bakım Modülü + Teknik Asistan Chatbot. Tahmini süre: ~2 hafta.
- **Pilot teslim tarihi güncellendi**: Ek F8 nedeniyle ~8 Haziran → **~22 Haziran 2026** olarak revize edildi (veya mevcut buffer'dan yenilir, duruma göre).
- **Stack'e yeni bağımlılıklar eklendi**: `sentence-transformers`, `faiss-cpu`.
- **Domain sözlüğüne yeni terimler eklendi**: Maintenance Schedule, Maintenance Checklist, Knowledge Base, Knowledge Article.
- **Pilot başarı kriterine ekleme**: Chatbot'un en az bir teknik soruya doğru cevap vermesi.

**Mimari etki:** Chatbot ve bakım modülü analytics loop'a eklenir. Critical loop'a dokunulmaz. Bilgi tabanı dosya sistemi tabanlıdır (Markdown + YAML), ayrı bir vektör indeksi oluşturulur.

**Önemli karar — LLM yok:** Chatbot bir dil modeli kullanmaz. Cevaplar tamamen bilgi tabanındaki dokümanlardan gelir. Semantic search (sentence-transformers) sadece kullanıcının sorusunu doğru dokümanla eşleştirmek için kullanılır. Bu karar bilinçlidir:
- Yerel çalışma prensibi korunur (internet gerektirmez)
- Halüsinasyon riski sıfırdır (cevaplar bire bir dokümandan gelir)
- N100 mini PC'de rahat çalışır (~250 MB model)
- Doküman kalitesi = cevap kalitesi, kontrol geliştiricide kalır

---

## 1. Ürün tanımı

Custos, üretim tesislerinde Modbus TCP üzerinden sensör verisi okuyan, bu verileri endüstri standardı asset şablonlarına bağlayan, KPI hesaplayan, ML tabanlı anomali tespiti ve eşik alarmı üreten, bakım süreçlerini yöneten ve teknik bilgi tabanı üzerinden operatöre asistan görevi gören, lokal çalışan bir edge izleme sistemidir.

**Temel kural değişmedi:** Sistem **sadece okur, asla yazmaz.** Prosese müdahale yoktur. Amaç: cihaz ve işletme koruması, erken uyarı, operasyonel görünürlük, bakım optimizasyonu.

**Pilot müşteri için tek cümlelik değer önerisi:** *"Fabrikanızın verilerini okuyan, sizin anladığınız dilde (pompa, chiller, kompresör) özetleyen, problem olmadan önce size haber veren ve ne yapmanız gerektiğini söyleyen lokal bir akıllı göz."*

---

## 2. Pilot müşteri

**Profil:**
- Sektör: Üretim fabrikası
- Haberleşme: Modbus TCP (mevcut)
- Otomasyon altyapısı: Regin + Expo SCADA (yenilenecek — Custos geliştiricisi ve ortağı tarafından)
- Sensör çeşitliliği: Yüksek (sıcaklık, basınç, akış, seviye, motor akımı/frekans, muhtemelen titreşim ve enerji sayaçları)
- İhtiyaç toplama yöntemi: Needs discovery — müşteri kendi ağzından söyledi, geliştirici önermedi
- Teslim tarihi: **~22 Haziran 2026 (F8 eklenmesiyle revize)**

**Pilot başarı kriteri:**
- Fabrikada kurulum gününden itibaren sistem kesintisiz çalışır
- Teknik ekip dashboard'u günlük kullanır
- En az bir gerçek uyarı üretir (eşik veya anomali)
- En az bir asset instance (örneğin bir pompa veya chiller) şablonla bağlanmış haldedir
- Chatbot en az bir teknik soruya bilgi tabanından doğru cevap verir
- En az bir bakım checklist'i alarm sonrası operatöre sunulmuştur

**Otomasyon bağlamı (yeni):** Pilot müşterinin otomasyon altyapısı Custos geliştiricisi ve ortağı tarafından Regin kontrolörler ve Expo SCADA ile yenilenecektir. Bu, Custos'un bilgi tabanı dokümanlarının birinci elden hazırlanmasını mümkün kılar — dokümanları yazan kişi sistemi kuran kişidir. Bu büyük bir avantajdır.

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
| **Maintenance Schedule** | Periyodik bakım takvimi | `maintenance_schedule` |
| **Maintenance Checklist** | Bakım/arıza kontrol listesi | `maintenance_checklist` |
| **Maintenance Task** | Tek bir bakım görevi | `maintenance_task` |
| **Knowledge Base** | Teknik bilgi tabanı (dokümanlar) | `knowledge_base` |
| **Knowledge Article** | Bilgi tabanındaki tek bir doküman | `knowledge_article` |

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

22. **Maintenance Sayfası** (yeni) — Bakım takvimleri, aktif checklist'ler, bakım geçmişi. Detaylar bölüm 4.8'de.

23. **Assistant Sayfası** (yeni) — Teknik asistan chatbot arayüzü. Detaylar bölüm 4.9'da.

### 4.7. Tasarım

24. **Visual Language** — Grafana ailesi, koyu tema, sabit sol sidebar + üst header + içerik grid. Component kütüphanesi: KPI card, chart panel (uPlot), status badge, data table, form elements. Referans ekran: Unraid Grafana dashboard stili.

### 4.8. Bakım modülü (yeni — F8a)

> **Bağlam:** Pilot müşterinin otomasyon altyapısı (Regin + Expo SCADA) Custos geliştiricisi ve ortağı tarafından kurulacaktır. Bakım prosedürlerini yazan kişi sistemi bilen kişidir. Bu, doküman kalitesini garanti eder.

25. **Periyodik Bakım Takvimleri** — Asset instance veya asset template bazında tekrarlayan bakım görevleri tanımlanır.
   - Periyot: günlük / haftalık / aylık / yıllık / custom (N gün)
   - Her görev için: başlık, açıklama, checklist adımları, tahmini süre
   - Yaklaşan bakım bildirimi (Web Push ile)
   - Dashboard'da "yaklaşan bakımlar" widget'ı (Overview sayfasında)
   - Bakım tamamlandığında operatör işaretler, zaman damgası kaydedilir

   **Örnek:** "Chiller kondenser temizliği — her 30 günde bir"

26. **Arıza Bazlı Kontrol Listeleri (Checklist)** — Alarm tipi veya alarm + asset template kombinasyonuna bağlı kontrol listeleri.
   - Bir alarm tetiklendiğinde, eşleşen checklist varsa operatöre otomatik sunulur
   - Checklist adımları sıralıdır, operatör tek tek işaretler
   - Tamamlanma durumu ve süresi kaydedilir (audit trail)
   - Checklist'ler YAML formatında tanımlanır, dashboard üzerinden de düzenlenebilir

   **Örnek:** Alarm "Kompresör deşarj basıncı yüksek" tetiklendiğinde:
   ```
   1. Kondenser fanının çalıştığını kontrol et
   2. Kondenser yüzeyini görsel olarak incele (kirlilik)
   3. Refrigerant basınç değerlerini oku
   4. Ortam sıcaklığını not al
   5. Fan motoru akımını kontrol et
   ```

27. **Alarm → Bakım Önerisi Bağlantısı** — Custos'un alarm sistemi ile bakım modülü arasında köprü.
   - Belirli alarm tipleri belirli checklist'lere eşlenir (`alarm_type` → `checklist_id`)
   - Alarm sayfasında tetiklenen alarm yanında "Kontrol Listesi" butonu görünür
   - Alarm acknowledge edilirken checklist tamamlanmış mı kontrol edilebilir (opsiyonel)
   - Tekrarlayan aynı alarm için "bu alarm son 7 günde N kez tetiklendi" uyarısı

**Maintenance Sayfası yapısı:**
- **Takvim sekmesi**: Yaklaşan ve geçmiş periyodik bakımlar, takvim veya liste görünümü
- **Checklist'ler sekmesi**: Tüm tanımlı checklist'ler, düzenleme, yeni ekleme
- **Geçmiş sekmesi**: Tamamlanmış bakım ve checklist kayıtları, filtreleme

### 4.9. Teknik asistan chatbot (yeni — F8b)

> **Temel karar: LLM kullanılmaz.** Chatbot bir büyük dil modeli (GPT, Claude, Llama vb.) çalıştırmaz. Cevaplar tamamen bilgi tabanındaki dokümanlardan gelir. Semantic search yalnızca kullanıcının sorusunu doğru dokümanla eşleştirmek için kullanılır. Bu sayede:
> - Sistem tamamen yerel çalışır, internet gerektirmez
> - Halüsinasyon riski yoktur — cevap dokümanda yoksa "bulamadım" der
> - Mini PC'de rahat çalışır (embedding modeli ~250 MB)
> - Cevap kalitesi = doküman kalitesi, kontrol geliştiricide

28. **Bilgi Tabanı (Knowledge Base)** — Dosya sistemi tabanlı doküman deposu.
   - **İki format desteklenir:**
     - **Markdown dosyaları** (.md): Uzun dokümanlar için. Ekipman çalışma prensipleri, sistem mimarisi açıklamaları, bakım prosedürleri. Her dosya bir `knowledge_article`.
     - **YAML soru-cevap dosyaları** (.yaml): Yapılandırılmış Q&A çiftleri. Sık sorulan sorular, kısa ve net cevaplar. Operatörün "X ne demek?" tarzı sorularına hızlı eşleşme.
   - Dokümanlar `data/knowledge/` dizininde tutulur
   - Her dokümanın frontmatter'ında: başlık, kategori (sistem/ekipman/arıza/bakım), ilgili asset template, etiketler
   - Yeni doküman eklendiğinde veya güncellendiğinde otomatik re-index (embedding güncelleme)

   **Doküman kategorileri:**
   - `sistem`: Otomasyon sistemi genel çalışma prensibi (Regin, Expo SCADA)
   - `ekipman`: Ekipman bazlı teknik bilgi (pompa, chiller, kompresör...)
   - `ariza`: Arıza tipleri, olası sebepler, kontrol adımları
   - `bakim`: Bakım prosedürleri, periyodik işler, yedek parça bilgileri

   **Doküman hazırlama sorumluluğu:** Dokümanlar Custos geliştiricisi ve ortağı tarafından otomasyon kurulumu sırasında hazırlanır. Custos bu dokümanları sadece indeksler ve sunar.

29. **Semantic Search Motoru** — Kullanıcı sorusunu bilgi tabanındaki en ilgili dokümanlarla eşleştirir.
   - **Embedding modeli**: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (Türkçe destekli, ~250 MB, CPU'da hızlı)
   - **Vektör indeksi**: FAISS (Facebook AI Similarity Search) — dosya tabanlı, lightweight
   - **Arama akışı**:
     1. Kullanıcı sorusu embedding'e çevrilir
     2. FAISS indeksinde en yakın N doküman/chunk bulunur (cosine similarity)
     3. Eşik üstü sonuçlar sıralanarak döndürülür
     4. Eşik altındaysa: "Bu konuda bilgi tabanında bir kayıt bulamadım."
   - **Chunk stratejisi**: Markdown dokümanlar başlık bazlı (## seviyesi) chunk'lanır. YAML dosyalarında her Q&A çifti bir chunk'tır.

30. **Chat Arayüzü** — Dashboard içinde chat paneli.
   - Sidebar'da "Asistan" menü öğesi → tam sayfa chat arayüzü
   - Mesaj baloncukları (kullanıcı / sistem), sohbet geçmişi (session bazlı)
   - Her cevabın altında: kaynak doküman linki ("Bu cevap şu dokümandan geldi: ...")
   - **Custos verisi ile zenginleştirme**: Eğer soru bir asset veya tag ile ilgiliyse, cevaba güncel Custos verileri eklenir:
     - Son okunan değerler
     - Aktif alarmlar
     - Son bakım tarihi
   - **Hızlı sorular**: Sık kullanılan sorular için buton şeklinde kısayollar (dashboard'a ilk girişte gösterilir)
   - HTMX ile sayfa yenilemesiz mesajlaşma

   **Örnek etkileşim:**
   ```
   Operatör: Chiller 1 alarm veriyor, ne yapmalıyım?

   Asistan:
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   Aktif Alarm: Deşarj basıncı yüksek (12.5 bar > 10 bar eşik)
   
   Kontrol Adımları:
   1. Kondenser fanının çalıştığını kontrol et
   2. Kondenser yüzeyini görsel olarak incele
   3. Refrigerant basınç değerlerini oku
   4. Ortam sıcaklığını not al
   
   Son Bakım: 15 gün önce (kondenser temizliği)
   
   Kaynak: ariza/chiller-yuksek-basinc.md
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   ```

**Olmayacak olanlar (chatbot sınırları):**
- LLM tabanlı serbest metin üretimi yok
- Doküman dışı cevap üretme yok (halüsinasyon riski)
- Sesli asistan yok
- Doküman içeriğini özetleme veya yeniden yazma yok — doküman olduğu gibi sunulur
- Çok turlu diyalog yok (her soru bağımsız aranır) — v1.1+'de düşünülebilir
- Yönetici panelinden doküman düzenleme yok — dokümanlar dosya sisteminde elle güncellenir

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
- **Semantic Search:** sentence-transformers (paraphrase-multilingual-MiniLM-L12-v2), faiss-cpu (yeni)
- **Background jobs:** APScheduler veya basit asyncio task'ları
- **Web Push:** `pywebpush` kütüphanesi

### 5.2. İki süreçli mimari (korunuyor)

- **`custos.critical`** — Düşük latency, kritik yol. Collector (Modbus polling) + Threshold Engine + Alarm dispatcher.
- **`custos.analytics`** — Daha yüksek latency tolere eder. Feature engine + ML + KPI motor + Dashboard + Web Push + **Bakım zamanlayıcı + Chatbot semantic search**.
- **`custos.shared`** — Config, logging, database abstract interface, domain models.

**Chatbot mimarisi notu:** Semantic search analytics loop içinde çalışır. Embedding modeli uygulama başlangıcında bir kez yüklenir ve bellekte kalır. Arama sorgusu ~50-100ms sürer (CPU). Critical loop'a etkisi yoktur.

### 5.3. Donanım (pilot için)

- Intel N100 veya N200 sınıfı **fansız Mini PC**
- **16 GB RAM** (sentence-transformers modeli ~500 MB RAM kullanır, toplam yeterli)
- **500 GB NVMe SSD**
- Gigabit Ethernet (fabrika LAN'ına Modbus TCP için)
- Marka seçimi pilot öncesine bırakıldı (Beelink / GMKTec / Topton / MINIX aday)

### 5.4. Deployment

- Docker Compose (mevcut docker-compose.yml genişletilecek)
- Systemd service (mini PC boot'ta otomatik başlasın)
- Lokal ağda erişim: `http://custos.local` (mDNS) veya statik IP

### 5.5. Bilgi tabanı dosya yapısı (yeni)

```
data/knowledge/
├── sistem/
│   ├── regin-genel-calisma.md
│   ├── expo-scada-arayuz.md
│   └── modbus-haberlesme.md
├── ekipman/
│   ├── pompa-sirkulasyon.md
│   ├── chiller-genel.md
│   └── kompresor-hava.md
├── ariza/
│   ├── chiller-yuksek-basinc.md
│   ├── pompa-dusuk-debi.md
│   └── motor-yuksek-akim.md
├── bakim/
│   ├── chiller-kondenser-temizlik.md
│   ├── pompa-mekanik-seal.md
│   └── filtre-degisim.md
└── sss/
    └── sik-sorulan-sorular.yaml
```

**Markdown frontmatter örneği:**
```yaml
---
title: Chiller Yüksek Deşarj Basıncı Arızası
category: ariza
asset_template: chiller
tags: [basinc, kompresor, kondenser, fan]
related_checklist: chiller-yuksek-basinc-checklist
---
```

**YAML Q&A örneği:**
```yaml
- soru: "Regin kontrolör üzerindeki kırmızı LED ne anlama gelir?"
  cevap: "Kırmızı LED haberleşme hatası gösterir. Modbus bağlantısını ve kablo bağlantılarını kontrol edin."
  kategori: sistem
  etiketler: [regin, led, haberlesme]

- soru: "Chiller COP değeri ne olmalı?"
  cevap: "Normal çalışma koşullarında COP 3.0-5.0 arasında olmalıdır. 2.5 altı verimsiz çalışmaya işaret eder."
  kategori: ekipman
  etiketler: [chiller, cop, verimlilik]
```

---

## 6. Aşama 4 feature sıralaması (10 haftalık plan — revize)

F1-F7 mevcut plan korunmuştur. F8 iki alt özellik olarak eklenmiştir. Toplam süre 8 → 10 haftaya çıkmıştır.

| Hafta | Feature | Kapsam |
|---|---|---|
| **1-2** | **F1: Visual Language + Dashboard Shell** | Tasarım dili, component kütüphanesi, layout shell, referans overview sayfası (sahte data). |
| **3** | **F2: Tag Modeli + Manuel CRUD + Sensors Sayfası** | `tag`, `tag_reading`, `tag_binding` şemaları. Migration. Manuel tag CRUD. Sensors sayfası. |
| **4** | **F3: Modbus Auto-Scan + Tag Browser** | Connection profile yönetimi, slave scan, register discovery, type inference, aday tag onay akışı. |
| **5** | **F4: Asset Template + Binding Wizard + Processes Sayfası** | Template library seed'i, Asset instance CRUD, Binding Wizard, Processes sayfası. |
| **6** | **F5: Threshold Engine + Alarm Sayfası + Logs** | ISA-18.2 state machine, threshold CRUD, alarm event'leri, Alarm sayfası, Logs sayfası. |
| **7** | **F6: KPI Motoru + KPI Sayfası + ML Anomaly** | KPI formül engine, asset başına Isolation Forest, günlük retrain job, KPI sayfası. |
| **8** | **F7: Web Push + Settings + Pilot Hazırlık** | VAPID keys, Service Worker, subscription, Settings sayfası, setup script'i, systemd service. |
| **9** | **F8a: Bakım Modülü** | Maintenance DB şeması, periyodik takvimler, checklist CRUD, alarm-checklist eşleme, Maintenance sayfası. |
| **10** | **F8b: Teknik Asistan Chatbot** | Bilgi tabanı indeksleme, sentence-transformers + FAISS kurulumu, semantic search API, chat arayüzü, Custos veri entegrasyonu. Son 2 gün buffer. |

**Zamanlama notu:** F8'in eklenmesiyle pilot teslim ~22 Haziran 2026'ya kayar. Alternatif olarak F8a ve F8b paralel çalışılabilir (bakım modülü DB + backend / chatbot ayrı) veya hafta sonları kullanılabilir. Kesin tarih haftalık check-in'lerde netleşecektir.

Her hafta sonunda: tüm testler yeşil, ruff + mypy temiz, commit mesajları Türkçe, git log düzgün, demo-able durum.

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
9. **Brief değişirse versiyon artar.** v1.4 → v1.5 ancak yazılı karar ile.
10. **Minimum hareketli parça.** Her yeni bağımlılık gerekçelidir.

---

## 8. Bilinen riskler ve azaltımları

| Risk | Olasılık | Etki | Azaltım |
|---|---|---|---|
| Modbus Auto-Scan sensör çeşitliliğinde yetersiz kalır | Orta-Yüksek | Orta | Manuel Tag ekleme fallback'i MVP'de var; auto-scan asistan olarak konumlanır, otomasyon değil |
| Eski PLC fast polling'i (>1 Hz) kaldıramaz, timeout üretir | Orta | Orta | Slave latency probing connection profile kaydında otomatik çalışır; kullanıcı seçtiği polling interval slave latency'sinden kısaysa uyarılır; fast polling budget pilot için 10 tag ile sınırlı |
| 10 haftalık plan sürtünmeyle kayar | Yüksek | Yüksek | F8 iki alt parçaya bölündü; F8b (chatbot) en son yapılır, gerekirse scope kesilebilir; haftalık check-in'ler |
| Pilot kurulum günü Modbus bağlantı sorunları | Orta | Yüksek | Kurulum öncesi uzaktan test (VPN üzerinden), fabrika ağ bilgisi önceden toplama, connection diagnostic sayfası MVP'de var |
| ML modeli ilk gün anlamlı sonuç üretmez | Yüksek | Düşük | Açıkça "öğrenme süresi gerekir" olarak iletişim; ilk hafta eşik alarmı öne çıkar, ML ikinci planda |
| Mini PC endüstriyel ortamda ısınma/toz sorunu | Düşük-Orta | Orta | Fansız N100/N200 seçimi; pilot sonrası IP-rated enclosure düşünülecek |
| Müşteri pilot sırasında yeni feature ister | Yüksek | Orta | v1.1 backlog dosyası açık tutulur, her istek yazılı kayda girer, hiçbiri MVP'ye alınmaz |
| Tek kişilik geliştirme tempo riski | Yüksek | Yüksek | Günlük kısa check-in (sabah 15 dk plan, akşam 15 dk review), uyku ve mola disiplini, yorgunluk sinyallerinde mola |
| Bilgi tabanı dokümanları zamanında hazır olmaz | Orta | Orta | Dokümanlar otomasyon kurulumu ile paralel hazırlanır; chatbot doküman olmadan da çalışır (boş sonuç döner); minimum viable doküman seti pilot için 5-10 makale |
| Sentence-transformers Türkçe sorularda düşük kalite verir | Düşük-Orta | Orta | Multilingual model seçildi; YAML Q&A dosyaları exact-match fallback sağlar; pilot öncesi test edilecek |
| Embedding modeli N100'de yavaş çalışır | Düşük | Düşük | Model bir kez yüklenir, arama ~50-100ms; FAISS indeksi bellekte; startup süresi artabilir (30-60s), kabul edilebilir |

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
- LLM tabanlı chatbot (yerel veya cloud) — v1 semantic search yeterliyse gerek olmayabilir
- Çok turlu chatbot diyalogu (bağlam takibi)
- Dashboard üzerinden bilgi tabanı düzenleme arayüzü
- Bakım raporları ve istatistikleri

---

## 10. Sıradaki adım

F1-F7 tamamlandı. Sırada **F8a: Bakım Modülü** feature prompt'u hazırlanacak (`claude_code_prompt_12_f8a_maintenance.md`). Ardından **F8b: Teknik Asistan Chatbot** (`claude_code_prompt_13_f8b_chatbot.md`).

**Doküman hazırlama paralel başlayabilir:** Otomasyon kurulumu sırasında bilgi tabanı dokümanları yazılmaya başlanır. Chatbot implementasyonu bitmeden bile dokümanlar `data/knowledge/` dizinine eklenebilir.

---

**Bu doküman pilot müşterinin ikinci görüşmesindeki gereksinimlerle güncellenmiştir. Değişmesi gerekirse versiyon artırılarak revize edilir. Sessizce düzenleme yapılmaz.**
