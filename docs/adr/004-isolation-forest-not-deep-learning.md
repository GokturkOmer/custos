# ADR-004: Anomali tespitinde Isolation Forest, derin öğrenme yok

**Tarih:** 2026-05-22
**Durum:** Kabul

## Bağlam

Karar proje başlangıcından (brief v1.0, CLAUDE.md ML kuralları) beri geçerlidir;
bu ADR onu yazıya geçirir ve sonradan yapılan deneylerle (wind pivot, Mayıs 2026)
doğrular.

Custos eşik alarmlarının yakalayamadığı "sinsi" sapmaları (yavaş bozulma,
çok-değişkenli mod kayması) ML ile tespit etmek ister. Ancak kısıtlar nettir:

- **Edge donanımı:** Pilot cihazı fansız mini PC; GPU yok.
- **Modeller cihazda eğitilmez:** Eğitim offline (geliştirici makinesinde),
  cihaz sadece çalıştırır (CLAUDE.md kuralı). Bu, determinizm ve test/train
  ayrımının korunması için kritiktir.
- **Açıklanabilirlik:** Operatör/teknik servis bir uyarının *neden* çıktığını
  anlayabilmeli.
- **İlk gün değer:** Sistem kurulduğu gün makul davranmalı; aylarca etiketli
  eğitim verisi beklenemez.

## Karar

Anomali tespiti **scikit-learn ailesi + istatistiksel yöntemlerle** yapılır:

- **Isolation Forest** (denetimsiz, ağaç-temelli) — ana çok-değişkenli detektör.
- **İstatistiksel detektörler** — MAD tabanlı robust z-score, EWMA/SPC kontrol
  kartları, yüke-koşullu (mode-aware) residual.
- **Çoklu-detektör birleşimi** — sonuçlar tek skorda birleştirilir.

**Derin öğrenme framework'leri yasaktır.** [`architecture_check.py`](../../scripts/architecture_check.py)
`DEEP_LEARNING` kuralı `torch`, `tensorflow`, `keras`, `jax` vb. doğrudan
import'unu CI + pre-commit'te bloklar. Modeller `scripts/train_anomaly_models.py`
ile offline eğitilir, cihaza `joblib` ile gönderilir; cihazda yalnızca `predict`
çalışır. Kritik Döngü'ye ML hiç girmez (`ML_IN_CRITICAL`, bkz. [ADR-001](001-two-process-architecture.md)).

**Bilinçli istisna:** Teknik asistan modülü (`analytics/assistant`) semantik arama
için `sentence-transformers` + `faiss` kullanır. Bu, dolaylı olarak PyTorch
çalıştıran yüksek seviye bir API'dir; doğrudan derin öğrenme framework'ü import'u
değildir ve yalnızca Analitik tarafta, asistan özelliğiyle sınırlıdır. Kritik
Döngü bu kütüphaneleri de import edemez.

## Sonuçlar

**Pozitif:**
- Hafif ve CPU'da hızlı; GPU'suz mini PC'ye uygun.
- Açıklanabilir: Isolation Forest skorları ve istatistiksel kontrol limitleri
  operatöre anlaşılır gerekçe sunar.
- Küçük, reprodüklenebilir bağımlılık; offline eğitimle test/train ayrımı net.
- Cihazda eğitim olmadığı için cihaz kaynakları ve davranışı öngörülebilir kalır.

**Negatif:**
- Teorik doğruluk tavanı, iyi ayarlanmış bir derin modelden düşük olabilir. Wind
  pivot deneyinde sklearn autoencoder ~0.50, referans makalenin PyTorch
  autoencoder'ı ~0.66 skor verdi. Bu fark, çoklu-detektör birleşimi ve pilot saha
  kalibrasyonu ile telafi edilir; yine de bilinçle kabul edilen bir sınırdır.
- Çok karmaşık çok-değişkenli arıza modları sığ modellerle kaçabilir; eşik
  alarmları ve cross-sensor kuralları bunu tamamlar.

## Alternatifler

- **Derin autoencoder / LSTM:** Daha yüksek doğruluk potansiyeli olsa da CLAUDE.md
  ile yasak; edge'de ağır, açıklanamaz ve pratikte cihazda eğitim/ayar gerektirir.
  Reddedildi (deneysel olarak wind pivot'ta ölçüldü, kazanç kuralları ihlal etmeye
  değmedi).
- **Cihazda online/sürekli eğitim:** Test/train ayrımını bozar, kaynak tüketir,
  determinizmi kaybettirir. Reddedildi.
- **ML'siz, yalnızca kural:** Anomali kapsamı dar kalır. Reddedildi — ancak eşik
  motoru zaten ML'den bağımsız tamamlayıcı katman olarak korunur.
