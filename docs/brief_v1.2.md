# Proje Brief — Endüstriyel Edge İzleme Sistemi

**Versiyon:** 1.2
**Proje adı:** Custos
**Tarih:** 8 Nisan 2026
**Durum:** Aşama 0 (Brief) tamamlandı, Aşama 1 (Proje İskeleti) başlıyor

---

## 1. Ürün

Otomasyon altyapısı olan endüstriyel tesislerde Modbus üzerinden sensör verisi okuyup, ML tabanlı anomali tespiti ve kullanıcı tanımlı eşik alarmları üreten, lokal çalışan bir edge sistem.

**Temel kural:** Sistem **sadece okur, asla yazmaz.** Prosese müdahale etmez, sadece gözlemler ve uyarır. Amaç: cihaz ve işletme koruması.

**Tek cümlelik değer önerisi:** *Deneyimli bir operatörün gözünden kaçanı, makine yakalar — daha hızlı, daha odaklı, aynı anda daha çok veride.*

---

## 2. Hedef segmentler

- **Segment A:** Eski jenerasyon, SCADA'sız veya küçük HMI'lı, kara düzen çalışan, Modbus haberleşmeli tesisler. Bugün arıza olduğunda tamir ediyorlar; deneyimli teknisyene bağımlılar.
- **Segment B:** SCADA'lı, daha yeni, çoklu protokollü tesisler. Modbus konuşan kısımlarına odaklanılır. Bugün insanlar SCADA'dan trend takibi ve manuel log analizi yapıyor.
- **Segment C (kapsam dışı):** Profesyonel SCADA'lı, danışmanlık alabilecek büyük tesisler. v2'ye ertelendi.

**Değer önerisi farklılığı:** Segment A için "hiçbir şeyin yoktu, şimdi gözün var." Segment B için "SCADA'nın görmediğini görüyorum."

---

## 3. Kullanıcılar

Operatör/çalışan + işletme sahibi/yönetici. Tek kurulum, multi-tenant yok.

---

## 4. MVP kapsamı

### Var olacaklar

- Modbus veri okuma (lokal, sadece okuma)
- ML tabanlı anomali tespiti (Katman 2: yumuşak uyarı)
- Kullanıcı tanımlı eşik alarmları (Katman 1: sert alarm)
- KPI hesaplama
- Lokal dashboard (yerel ağdan tarayıcıyla erişim)
- Geçmiş veri saklama (kayıpsız sıkıştırılmış ham veri)
- Veri dışa aktarımı (CSV + Parquet)
- Ekipman görsel şablon kütüphanesi (sadece UI layout, içinde gömülü akıl yok)
- Bildirim kanalları: Telegram + e-posta
- Kullanıcı geri bildirim döngüsü ("doğru/yanlış alarm" işaretleme)
- Sistem self-monitoring (CPU, RAM, disk, sıcaklık, son okuma zamanı)

### Yok

- LLM / chat botu
- Bulut altyapısı, multi-tenant, çoklu cihaz yönetimi
- Mobil uygulama
- OTA otomatik güncelleme (manuel SSH + git pull + docker-compose)
- Karmaşık kullanıcı hesap sistemi
- Otomatik tesis tanıma / kurulum sihirbazı
- WhatsApp entegrasyonu
- Fiziksel buzzer / röle çıkışları
- Profinet, EthernetIP, diğer protokoller
- Windows desteği
- Modbus konfigürasyon şablonları (ilk pilotta elle yapılır, v2)
- Deadband (ham veri korunur — ancak continuous aggregates üzerinden özetler tutulur)
- Derin öğrenme modelleri

---

## 5. Strateji

**"Test edelim" yaklaşımı.** 3 ay sonunda ürün sahaya pilot olarak gider, satış olarak değil. Tesis kabul etmezse alternatif tesise gidilir; o da olmazsa geçmiş veri talep edilir. Bu rol değişikliği (satıcı → araştırmacı/geliştirici) baskıyı düşürür ve hata bulmayı meşrulaştırır.

**Pilot sonrası ödeme stratejisi:** İlk pilot başarılı olduktan sonra ayrıca konuşulacak (şu an karar değil, hatırlatma).

---

## 6. Başarı kriterleri

### Teknik MVP

5 katmanlı test piramidini geçer:
1. **Birim testler** — kod seviyesi
2. **Modbus simülatörü ile entegrasyon testi** — gerçek donanım yokken
3. **Enjekte arıza testi** — gerçek tesiste, manuel sensör manipülasyonu (Katman 1 doğrulama)
4. **Geriye dönük (replay) test** — tesisin geçmiş loglarını sisteme verip ML'in bilinen arızaları yakalayıp yakalamadığına bakmak (Katman 2 doğrulama)
5. **Canlı gözlem dönemi** — sistem 2-4 hafta sessizce dinler, log incelenir

### Ticari MVP

Aşamalı:
- **Hafta 1-2 — Tanım C (kullanım):** Kullanıcı sistemi açıyor mu, bakıyor mu? Açmıyorsa UX sorunu, devam etme.
- **Hafta 3-4 — Tanım B (kaldırma testi):** "Kaldırayım mı?" diye sor, "hayır kalsın" cevabı yeşil ışık.
- **Ay 2 — Tanım A (ödeme):** Fiyat konuş, ödeme iste. Bu noktada "ürünüm var" denebilir.

---

## 7. Mimari prensipler (sabit)

1. **Lokal-öncelikli, internet bağımlılığı yok.** Tüm işlem cihaz üzerinde.
2. **Sadece okur, asla yazmaz.** Sisteme müdahale yok.
3. **İki katmanlı bildirim.** Katman 1 (deterministik, kullanıcı eşikleri) ve Katman 2 (ML, olasılıksal) iki ayrı kod yolu, iki ayrı güven seviyesi.
4. **İki süreçli mimari (arıza yalıtımı için):**
   - **Critical loop:** Collector + Threshold Engine + sert alarm gönderimi. Minimum bağımlılık, maksimum güvenilirlik.
   - **Analytics loop:** Feature Engine + ML inference + Dashboard + Notifier (yumuşak). Critical loop'tan bağımsız çöker.
5. **Donanım-agnostik yazılım.** Donanıma özgü kod izole bir katmanda (hardware abstraction layer).
6. **"En küçük ortak payda" tasarımı.** Önce mini PC, Pi MVP sonrası. ML modelleri Pi 16GB sınıfını hedefleyerek tasarlanır.
7. **Linux-only deployment.** Windows desteği yok.
8. **Konteynerleştirme (Docker).** Geliştirme ve deployment köprüsü. Pi sınıfı bedeller donanım yükselterek değil, optimize ederek yönetilir.
9. **Veritabanı katmanı abstract arayüz arkasında.** TimescaleDB ana tercih, ama değiştirilebilir.
10. **Train offline, infer online.** Modeller laptop'ta eğitilir, edge cihazda sadece çalıştırılır.
11. **Eğitim/validasyon/test veri ayrımı kutsaldır.** Test seti asla kirletilmez.
12. **Solo kurucu için: minimum hareketli parça.**
13. **Loglanmayan şey olmamış sayılır.** Yapılandırılmış loglama (structlog).
14. **Graceful degradation.** Bir bileşen çökerse diğerleri çalışmaya devam eder.
15. **Ham veri kayıpsız korunur.** Akademik işbirliği için v2'ye köprü.

---

## 8. Bileşen mimarisi

5 mantıksal modül, iki süreçte dağıtılmış:

**Critical Loop süreci:**
1. **Collector** — Modbus okuma, donanım soyutlaması, watchdog
2. **Threshold Engine** — Kullanıcı eşik kontrolü (Katman 1)
3. **Hard Notifier** — Sert alarm gönderimi (Telegram, e-posta)

**Analytics Loop süreci:**
4. **Storage** — TSDB (ham veri + continuous aggregates + features) + ilişkisel (config, etiketler, kullanıcılar)
5. **Analyzer**
   - Feature Engine (ham veriden özellik üretir)
   - Inference Pipeline (hızlı katman: 10 dk pencere; yavaş katman: 24 saat pencere)
   - Model Registry
   - Feedback Collector
6. **Soft Notifier** — Yumuşak uyarı dağıtımı (asenkron kuyruk)
7. **Dashboard / API** — Web arayüzü + REST API + WebSocket canlı veri akışı

**İletişim:** Süreç içinde fonksiyon çağrıları. Süreçler arası: paylaşılan veritabanı. Notifier asenkron kuyruk üzerinden çalışır.

**Veri katmanları:**
- **Katman 1 — Ham zaman serisi:** `timestamp | sensor_id | value | quality_flag`
- **Katman 2 — Özellik tablosu:** `timestamp | feature_name | feature_value | window_size`
- **Katman 3 — Etiket tablosu:** `timestamp_start | timestamp_end | event_type | confidence | source | notes`
- **Bonus — Model registry:** Model versiyonları, parametreleri, performansı

---

## 9. Teknoloji yığını

| Katman | Seçim |
|---|---|
| Dil | Python 3.11+ |
| Modbus | pymodbus 3.x |
| Veritabanı | TimescaleDB (PostgreSQL extension), abstract arayüz arkasında |
| API/Backend | FastAPI |
| Frontend | HTMX + Alpine.js + uPlot + Jinja2 (form/navigasyon için), WebSocket + uPlot (canlı veri için) |
| ML | scikit-learn + numpy + pandas + joblib + scipy |
| Zamanlama | asyncio tabanlı kendi döngülerimiz (APScheduler değil) |
| Async kuyruk | asyncio.Queue |
| Konfigürasyon | pydantic-settings + TOML |
| Loglama | structlog |
| Test | pytest + pytest-asyncio + pytest-cov |
| Lint/format | ruff + mypy |
| Container | Docker + docker-compose, ilk pilot için x86 (Pi multi-arch sonra) |
| Veri export | pyarrow (Parquet) + pandas (CSV) |
| Bildirim | httpx (Telegram) + smtplib (e-posta) |
| Process | systemd + docker restart:always |
| Self-monitoring | psutil |
| Güvenlik | HTTPS (self-signed), şifre zorunluluğu, audit trail |

**Yığının ruhu:** Basit, sıkıcı, dayanıklı. Hiçbir bileşen "havalı" değil. Hepsi yıllarca üretimde kanıtlanmış.

---

## 10. ML stratejisi

**İki paradigma, zamana yayılı:**

- **Gün 0 modeli (denetimsiz):** Isolation Forest. Etiket istemez, kurar kurmaz çalışır. Çıktıları kullanıcı doğrulamasıyla etiket biriktirir.
- **Gün 30+ modeli (denetimli):** Random Forest, regression, decision trees. Yeterli etiket biriktiğinde devreye alınır.

**İki katmanlı pencere:**

- **Hızlı katman (10 dakika):** Anlık anomali, ring buffer'da bellekte, her 5-10 saniyede çalışır.
- **Yavaş katman (24 saat):** Trend anomalisi, Storage'dan sorgulanır, her 5-15 dakikada çalışır.

**Modeller listesi (santral deneyiminden gelen):**
Anomaly detection, regression, logistic regression, decision trees, random forest, isolation forest. Hepsi scikit-learn ailesinde, derin öğrenme yok.

---

## 11. Endüstriyel ekstralar

Sıradan SaaS projelerinde olmayan ama endüstriyel için kritik:

1. **Power-loss recovery:** Veritabanı bütünlüğü (WAL), checkpoint mantığı.
2. **Time sync:** NTP zorunlu, tüm zaman damgaları UTC.
3. **Modbus watchdog:** Ölü TCP bağlantılarını tespit edip yeniden bağlanma.
4. **Graceful degradation:** Her bileşen, diğerleri olmadan minimum işlev.
5. **Audit trail:** Kim, ne zaman, hangi eşiği değiştirdi.
6. **Hot reload:** Konfigürasyon değişikliğinde sistem yeniden başlamaz.
7. **Sistem self-monitoring:** Kendi sağlığını izler ve gerekirse alarm üretir.

---

## 12. Veri saklama politikası

- **Ham veri kayıpsız sıkıştırılmış olarak saklanır.** Bunun üzerine, performans ve uzun-dönem analiz için TimescaleDB continuous aggregates kullanılır: dakikalık, saatlik ve günlük özetler otomatik hesaplanıp ayrı tablolarda tutulur. Ham veri silinmez, deadband yok.
- **Beklenen yıllık disk tüketimi:** ~12-25 GB (100 sensör, 1 sn okuma, sıkıştırma sonrası).
- **Donanım gereksinimi:** SSD zorunlu (SD kart desteklenmiyor — yazma yorgunluğu).
- **Yedekleme:** Lokal ikinci disk veya yerel ağ paylaşımına kopya, kullanıcı tarafından konfigüre edilir. Bulut yok.
- **Dışa aktarma:** CSV + Parquet (akademik işbirliği için hazır).

---

## 13. Kısıtlar

| Kısıt | Değer |
|---|---|
| Zaman | ~30 saat/hafta, tam zamanlı |
| Runway | 3 ay |
| Donanım bütçesi | 30.000 TL (test PLC + sensörler dahil ~3.000-5.000 TL ayrılır) |
| Geliştirme makinesi | i7 14. nesil, 16GB RAM, RTX 5070, Docker hazır |
| İlk pilot donanımı | x86 mini PC (16GB RAM önerilen) |
| Test tesisi erişimi | 3. ay sonunda, "test edelim" yaklaşımıyla |
| İnsan kaynağı | Solo + tanıdık mühendis ağı + tanıdık tesis sahipleri |

**Hedef ölçek:** 100 sensör (200'e ölçeklenebilir), saniyede 1 okuma.

---

## 14. Çalışma kuralları

- **Strateji + yorumlama + karar = Claude.ai (burada, benimle).**
- **Kod yazımı + test + pipeline = Claude Code (orada, ayrı oturumlarda, taze context).**
- **Veri envanteri + etiketleme + saha gözlemi = Göktürk (insan işi, devredilemez).**

---

## 15. Bilinen riskler ve henüz çözülmemiş sorular

Bunlar sahaya/inşaya geçince netleşecek. Şimdi karar verilmesine gerek yok ama unutulmasın:

1. **TimescaleDB Pi'da nasıl davranacak?** Mini PC'de sorun yok, Pi tarafında belirsizlik var. Veritabanı katmanı abstract olduğu için gerekirse değiştirilebilir.
2. **WebSocket + uPlot 200 sensör için akıcı mı?** İlk dashboard prototipinde test edilecek.
3. **ML modeli saatlik ortalama veriden anlamlı sonuç çıkaracak mı?** Feature Engine ilk çalıştığında görülecek.
4. **Modbus konfigürasyonu pilot tesiste ne kadar sürtünme yaratacak?** İlk kurulum elle yapılacak, "1 gün gider" varsayılıyor.
5. **Pilot sonrası ödeme modeli ne olacak?** İlk pilot başarılı olduktan sonra konuşulacak.
6. **Bildirim kanalında WhatsApp ileride istenirse ne yapılacak?** v2'ye not edildi, MVP'de Telegram + e-posta yeterli.

---

## 16. Sıradaki adımlar

**Aşama 1 — Proje İskeleti (Claude Code ile):**
- Repo yapısı ve klasör organizasyonu
- CLAUDE.md (Claude Code için kalıcı talimat dosyası — Türkçe yorumlar, datetime UTC, dosya düzenleme kısıtları, vb.)
- Docker + docker-compose konfigürasyonu (iki süreçli yapı)
- pyproject.toml + ruff + mypy + pytest temel konfigürasyonu
- pre-commit hook'ları
- TimescaleDB için ilk şema migration'ı
- Veritabanı abstract arayüzü
- En küçük çalışan iskelet ("walking skeleton"): boş Collector → boş Storage → boş Dashboard, uçtan uca veri akışı (sahte veri ile)

İskelette **hiçbir gerçek özellik** yok. Sadece "bu projede iş nasıl yapılır" sorusunun yazılı cevabı.

**Aşama 2 — Walking Skeleton:** Sahte Modbus simülatöründen → veritabanına → dashboard'a uçtan uca veri akışı.

**Aşama 3 — Feature'lar:** Her özellik test ile birlikte, "önce test, sonra implementasyon" döngüsünde.

---

**Bu doküman Aşama 1'in başlangıcında dondu. Değişmesi gerekirse, açıkça revize edilir ve versiyon numarası artırılır.**
