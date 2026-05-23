# ADR-003: TimescaleDB tabanlı katmanlı historian

**Tarih:** 2026-05-22
**Durum:** Kabul

## Bağlam

Çekirdek veri katmanı kararı brief v1.0'dan beri TimescaleDB'dir; katmanlı
historian + Parquet arşiv yapısı F11 (brief v1.7, 21 Nisan 2026) ile
derinleştirilmiştir. Bu ADR ikisini birlikte yazıya geçirir.

Custos lokal bir edge cihazda yüksek hacimli zaman serisi verisi tutmak zorunda:
**200+ tag × 1 Hz ≈ günde 17 milyon okuma.** Gereksinimler:

- **Uzun saklama:** 365 gün ham + 3 yıl dakika + sınırsız saat agregatı (veri =
  AR-GE/ESG için biriken bir varlık vizyonu).
- **Edge donanımı:** Fansız mini PC, 2 TB NVMe SSD; düşük operasyon yükü.
- **Sabit sorgu performansı:** Dashboard grafikleri her zoom seviyesinde (1 saat
  ↔ 1 yıl) hızlı yanıt vermeli.
- **Veri dışarı çıkmaz:** Cloud/S3/NAS senkronizasyonu yok; her şey lokal.

## Karar

**PostgreSQL 16 + TimescaleDB 2.25** üzerine kurulu **katmanlı historian:**

| Katman | Yapı | Saklama | Tazeleme |
|---|---|---|---|
| `tag_readings` | Hypertable, chunk = 1 gün, compress (segmentby `tag_id`) 7 gün sonra | 365 gün | — |
| `tag_readings_1min` | Continuous aggregate (AVG/MIN/MAX/STDDEV/COUNT) | 3 yıl | 5 dk |
| `tag_readings_1hour` | Hierarchical CA (1min'den türetilir) | Sınırsız | 30 dk |
| Parquet arşiv | Aylık columnar snapshot (LZ4), `/var/custos/archive/YYYY-MM/` | Sınırsız | Aylık |

Tamamlayıcı kararlar:

- **Auto-resolution query** (`query_readings_auto`): Sorgu penceresine göre doğru
  katmandan okur (≤1 saat → ham, ≤1 gün → 1min, >1 gün → 1hour). Tüketici hangi
  katmanın kullanıldığını bilmek zorunda değildir; her katman homojen
  `list[TagReading]` döndürür.
- **Query guard:** `(tag_count × time_range_days)` eşiği aşılırsa sorguyu bir üst
  agregata zorlar veya reddeder (HTTP 400). Kötü/aşırı sorgulara karşı koruma.
- **Tek DB erişim noktası:** Tüm SQL yalnızca `shared/database.py` içindeki soyut
  `DatabaseInterface`'te yazılır. Modüller raw SQL atamaz. Bu, statik denetimle
  zorlanır ([`architecture_check.py`](../../scripts/architecture_check.py)):
  `SQL_OUTSIDE_DATABASE` ve `DB_DRIVER_NON_ASYNCPG` kuralları (yalnızca asyncpg).

## Sonuçlar

**Pozitif:**
- Native compression ile disk verimi: 365 günlük ham veri (~90 GB/yıl) 2 TB
  SSD'ye rahatça sığar; eski chunk'lar otomatik sıkışır.
- Sabit sorgu performansı: Auto-resolution sayesinde 1 yıllık pencere bile
  agregat katmandan milisaniyeler içinde gelir (30 günlük chart < 200 ms hedefi).
- Düşük ops yükü: Retention ve compression TimescaleDB native job'larıdır;
  manuel temizlik gerekmez.
- Future-proof müşteri erişimi: Parquet (Apache Arrow) TimescaleDB'den bağımsız,
  açık formattır; müşteri verisine 20 yıl sonra da herhangi bir araçla erişebilir.

**Negatif:**
- TimescaleDB ekstra bir kurulum bağımlılığıdır (PGDG + packagecloud repo).
  `deploy/setup.sh` bunu idempotent biçimde halleder.
- 1hour katmanında STDDEV yaklaşıktır (60 dakikanın ağırlıksız ortalaması, exact
  pooled-variance değil). Trend için yeterli; kabul edildi.
- Tek node — yüksek erişilebilirlik (HA) yoktur. Lokal tek kurulum modeli için
  bilinçli kapsam sınırı.

## Alternatifler

- **Düz PostgreSQL (TimescaleDB'siz):** Bu hacimde otomatik chunk'lama,
  compression ve continuous aggregate olmadan tablo şişer, sorgu yavaşlar.
  Reddedildi.
- **InfluxDB:** Ayrı bir ekosistem; SQL yok, ilişkisel domain modeliyle (tag,
  instance, alarm, bakım) entegrasyonu zayıf, sürüm/lisans belirsizliği. Reddedildi.
- **SQLite:** Tek dosya basitliği cazip ama bu yazma hacmi + eşzamanlı erişim +
  compression eksikliği bu ölçekte yetersiz. Reddedildi.
- **Bulut zaman serisi DB'si:** "Veri lokal kalır, dışarı çıkmaz" temel ürün
  ilkesine aykırı. Reddedildi.
