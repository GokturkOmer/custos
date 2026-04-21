# Custos — Altyapı & Vizyon Özeti

**Tarih:** 20 Nisan 2026
**Versiyon:** v1
**Kapsam:** Brief v1.6 + 20 Nisan 2026 sohbetindeki vizyon/mimari kararları
**Statü:** Onaylandı (20 Nisan 2026). Ardından brief v1.7 ile resmileşecek.

---

## 1. Stratejik konumlanma

Custos üç katmanlı bir ürün olarak konumlanır:

**Katman 1 — Anlık izleme (mevcut):** Modbus tag okuma, alarm, ML anomali, bakım, chatbot. AVM pilotunda (5 Haziran 2026) teslim edilecek.

**Katman 2 — Lokal historian (yeni, pilot ile birlikte):** Yüksek çözünürlüklü uzun vadeli sensör verisi deposu. "Trend sistemi yok ya da görünmez" olan işletmelere **veri kaydı + geriye dönük inceleme** değer önerisi. Veri lokal kalır, dışarı çıkmaz.

**Katman 3 — AR-GE veri temeli (vizyon, v1.1+):** Saha verisinin 3-5 yıl biriktiği, gelecekte ML modelleri / agent'lar / sürdürülebilirlik raporları / sektör benchmark'ı üretilecek varlık. Bugün bir ürün değil, **bugünden kilit taşları atılması gereken bir gelecek opsiyonu**.

**Satış dili:** "Verileriniz dışarı çıkmaz, sizde kalır, ileride sizin için değer üretecek varlığa dönüşür."

---

## 2. Mimari kararlar

### 2.1. Veri katmanları (historian yapısı)

| Katman | İçerik | Saklama süresi | Yaklaşık boyut (200 tag × 1/s) |
|---|---|---|---|
| **Ham tablo** | Saniye çözünürlüklü okumalar | **365 gün** | ~90 GB |
| **Dakika agregat** | AVG/MIN/MAX/STDDEV (continuous aggregate) | **3 yıl** | ~1 GB |
| **Saat agregat** | AVG/MIN/MAX/STDDEV | **Sınırsız** | ~50 MB / 10 yıl |
| **Parquet arşiv** | Aylık snapshot — columnar, sıkıştırılmış, future-proof | **Sınırsız** (müşteri isterse siler) | ~10 GB / yıl |

**Varsayılan = cömert.** Müşteri Settings'ten ham süreyi kısaltabilir veya "auto-clean off" yapar.

### 2.2. Donanım

- **Pilot başına 2 TB NVMe SSD** (brief v1.6'daki 500 GB'dan yükseltme)
- Geri kalanı aynı: Intel N100/N200 fansız mini PC, 16 GB RAM, Gigabit Ethernet
- Ek maliyet ~80-150 € — teklife açıkça girecek kalem

### 2.3. Parquet arşiv

- **Yer:** `/var/custos/archive/YYYY-MM/` — mini PC içinde müşterinin erişebildiği klasör
- **Sıklık:** Aylık (her ayın 1'inde bir önceki ay arşivlenir)
- **İçerik:** Ham tablo + dakika agg + saat agg — hepsi ayrı Parquet dosyaları
- **Amaç:** Müşteriye "verim dosya olarak elimde" güveni + TimescaleDB'den bağımsız okunabilir format (Apache Arrow ekosistemi 20 yıl sonra da yaşar)

### 2.4. Uzak erişim modeli

- **Veri sync YOK** (cloud, S3, NAS hiçbirine veri gönderimi yapılmaz — pilot'ta)
- **Müşteri onaylı VPN erişimi VAR** (bakım/destek için, geliştirici uzaktan sisteme bağlanır)
- Bu, "veriniz dışarı çıkmıyor" argümanını bozmaz — sadece geliştiricinin gözü erişir, dosya aktarımı yapmaz
- İleride (v1.1+) opsiyonel müşteri kontrollü cloud yedekleme konuşulabilir — ayrı kalem

### 2.5. Query & dashboard

- **Auto-resolution query:** Zoom seviyesine göre doğru katmandan okur (≤1 saat = ham, 1 saat–1 gün = dakika, 1 gün–1 ay = saat, >1 ay = saat/gün). Kullanıcı ve yazılım için şeffaf, performans her koşulda sabit.
- **Query guard:** Aşırı geniş sorguları (>200 tag × >7 gün × ham) reddeder veya aggregate'e yönlendirir. Kötü niyetli veya yanlış kullanım korumadır.

### 2.6. Collector tarafı

- **Sıralı Modbus okuma paralelleştirilecek** — `asyncio.gather` + per-host bounded concurrency (5–10). 50–100 tag × 1 Hz senaryosunu rahatlatır; gerçek tag sayısı netleşince ince ayar.
- **Fast polling budget enforcement** — log değil, aktif kilit. Budget aşılırsa tag aktive edilemez, kullanıcıya net mesaj.

### 2.7. TimescaleDB native özellikler (ilk defa ayarlanacak)

- `set_chunk_time_interval` — 1 gün
- `ALTER TABLE ... SET (timescaledb.compress, compress_segmentby='tag_id')`
- `add_compression_policy` — 7 gün sonra otomatik
- `add_retention_policy` — ham için 365 gün, dakika agg için 3 yıl
- Continuous aggregate migration'ları — dakika + saat + (opsiyonel) gün katmanları

---

## 3. AR-GE veri kullanım odakları

Veri altyapısı dört paralel vizyona açık olmalı:

1. **Ekipman ömür döngüsü / sürdürülebilirlik** → yıllık AVG/MAX/STDDEV + bakım olayları ile korelasyon
2. **Enerji verimliliği / ESG raporlaması** → kWh, COP, cos φ aggregate'leri + CBAM hazırlığı
3. **Arıza öngörüsü (predictive maintenance)** → feature engineering için ham + dakika + bakım event'leri
4. **Çapraz-tenant benchmark** (anonim) → ikinci ürün fikri ("Custos Benchmark"), v1.1+

Ortak altyapı: **tier'lı historian + Parquet export + event'ler (bakım, alarm) ayrı zaman damgalı tabloda**. Tamamı mevcut tasarımda karşılanıyor.

---

## 4. İş modeli çerçevesi

- **Göktürk:** Yazılım üretim + uzaktan bakım + teknik ilişki. Tek sorumlu yazılımdan.
- **Strategic channel partner (otomasyon firması sahibi):** Satış kanalı + saha kurulumu + müşteri ilişkisi zemini. AVM pilot teklifi bu kanaldan geldi. Resmi şirket ortaklığı değil.
- **Model:** Revenue share veya referral fee — netleştirilecek (açık kalem, §6).
- **Müşteri (AVM CEO):** Ücretli pilot, ürünü bilfiil isteyen taraf.

**Şirket durumu:** Henüz kurulu değil. AVM pilot geliri ile kurulacak. Hibe başvuruları (TÜBİTAK BİGG, KOSGEB) şirketleşme takvimi ile hizalanacak.

---

## 5. Teknik iş paketleri

Pilot öncesi (5 Haziran'a kadar) yapılacaklar. Detay takvim iş planı belgesinde.

| # | Paket | Efor | Öncelik |
|---|---|---|---|
| **A** | TimescaleDB retention + compression + chunk migration | 2–3 saat | Kritik |
| **B** | Continuous aggregates (1min/1hour/1day) migration + query fonksiyonları | 1 gün | Kritik |
| **C** | Auto-resolution query API (`query_readings_auto`) | 3–4 saat | Kritik |
| **D** | Dashboard chart handler'ı auto-resolution'a bağlama + gather parallelize | 4–5 saat | Yüksek |
| **E** | Parquet aylık arşiv job (APScheduler + pyarrow) | 1 gün | Yüksek |
| **F** | Settings UI: ham retention seçici + "auto-clean off" anahtarı + disk doluluk uyarısı | 1 gün | Yüksek |
| **G** | Collector paralelleştirme + fast polling budget enforcement | 1 gün | Yüksek |
| **H** | Query guard (kötü sorgu koruma) | 2–3 saat | Orta |

**Toplam:** yaklaşık **5–6 iş günü**.

---

## 6. Açık kalemler (ayrı konuşma gerekir)

1. **Cloud sync / müşteri kontrollü yedekleme** — v1.1+ kararı, modeli belirsiz
2. **"Custos Benchmark" — ikinci ürün vizyonu** (kullanıcının mevcut bir başka projesi ile kesişiyor, sonra konuşulacak)
3. **Ortak ile gelir paylaşımı modeli** — AVM pilot sonrası netleşir
4. **Veri mülkiyeti sözleşmesi** — kullanıcı avukatıyla yürütüyor; teknik kullanım hakları çerçevesi ayrı belgede
5. **Şirketleşme takvimi + hibe başvuru sırası** — iş planı belgesinde ele alınacak
6. **Gerçek tag sayısı + polling mix senaryosu** — ortaktan cevap bekleniyor, donanım ve collector parametreleri onunla netleşir

---

## 7. Brief v1.7 için değişiklik notları

Bu özet resmileşince brief v1.6 → **v1.7** güncellemesi şu başlıklarda yapılacak:

- §1 ürün tanımına "lokal historian" rolü
- §3 domain sözlüğüne: Continuous Aggregate, Parquet Archive, Retention Policy, Auto-Resolution Query
- Yeni §4.12 **F11: Historian & Retention Stack** — A–H iş paketleri ile
- Yeni §5.7 veri katmanları + donanım revizyonu (2 TB SSD)
- §6 aşama sıralaması: F11 hangi haftalara paralel serpiştirilecek (iş planı belirleyecek)
- §8 risklerine: "disk büyüme hızı — retention UX'i yeterince net anlatılmazsa müşteri şaşırır"
- §11 yeni çalışma kuralı: "veri saklama varsayılanları her zaman cömert; kısıtlama müşterinin bilinçli kararıdır"

---

**Bu belge sohbetin fotoğrafıdır; brief v1.7 yazılınca onunla birlikte yaşar. Değişiklikler brief üzerinden takip edilir, bu belge revize edilmez (çalışma kuralı: brief değişirse versiyon artar).**
