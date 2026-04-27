---
title: "Enerji analizör kalibrasyonu"
category: bakim
asset_template: energy_analyzer
tags: [energy_analyzer, enerji, kalibrasyon, akim_trafosu, gerilim]
versiyon: 1.0
yazar: Göktürk
tarih: 2026-04-28
kaynak: IEC 61557-12 + Schneider PowerLogic + Iskra MT174 servis kılavuzları
---

# Enerji analizör kalibrasyonu

Enerji analizörü (Türkçe yaygın isim: enerji ölçer / network analyser), AVM'de elektrik tüketimini izlemek için panellere yerleştirilir. AVM'de tipik 20-100 nokta — ana giriş, mağaza, mekanik (chiller / pompa / aydınlatma). Ölçüm doğruluğu sigorta ve KPI takibi için kritik.

## Niçin kalibrasyon

- Akım trafoları (CT) yıllık %0.5-1 sapma yaşar.
- Gerilim trafoları (varsa) daha kararlı ama yine de izleme gerekir.
- Yanlış ölçüm → yanlış fatura / yanlış KPI / yanlış anomali tetiklemesi.
- Yasal: Türkiye'de büyük tüketici tarifesinde sayaç doğrulama düzenli rapor gerektirir.

## Sapma belirtileri

- Custos `active_power_kw` toplamı ana gelen sayaç ile uyumsuz (>%2 fark sürekli).
- Aynı tip iki ekipmanın aktif güç değerleri normalde benzer iken biri belirgin farklı (örn. iki AHU aynı çalışıyor ama biri 5 kW okuyor diğeri 3 kW).
- Cosφ (power factor) değeri 0.5 altı veya 1.05 üstü — sensör problemi.
- Gerilim okuması gerçek voltmetre ile karşılaştırıldığında %3+ fark.

## Kalibrasyon türleri

### A) Ofset kalibrasyonu (zero check)

CT açık devre / sıfır akım durumunda analizörün okuduğu değer. Sıfırdan farklıysa elektronik ofset var, üretici menüsünden kalibre.

### B) Span kalibrasyonu (gain check)

Bilinen referans akım ile karşılaştırma. Pens ampermetre + analizör değeri farkı birim olarak değerlendirilir.

### C) Faz kalibrasyonu

3 fazlı sistemde her fazın doğru sırada bağlandığını ve fazlar arası açıların 120 ° olduğunu kontrol.

### D) CT-VT oranı kalibrasyonu

Analizör menüsünde CT primary ve secondary değerleri (örn. 200/5 A) doğru girilmiş mi? Yanlış oran → tüm ölçümlerde sabit çarpan hatası.

## Kalibrasyon prosedürü

1. **Periyot:** AVM ana giriş için yılda 1 zorunlu, mağaza ölçümleri için 2-3 yılda 1.
2. **Hazırlık:** Pens ampermetre, multimetre, kalibrasyon kayıt formu, üretici kılavuzu.
3. **Güç emniyeti:** Mümkünse panel kapalı; kapalı değilse personal protective equipment (PPE) zorunlu.
4. **Ölçüm:** Her faz için akım, gerilim, cosφ, frekans değerlerini referans cihazla karşılaştır.
5. **Kalibrasyon:** Sapma %0.5+ ise üretici menüsünden ayar (gain veya offset).
6. **Doğrulama:** Kalibrasyon sonrası tekrar ölç, fark %0.2'nin altında olmalı.
7. **Kayıt:** Custos bakım sayfasına kalibrasyon notu — tarih, personel, sapma değerleri, doğrulama.

## Custos rolü

- **Tüm tag'lar zaman damgalı kayıt:** Kalibrasyon öncesi/sonrası baseline çıkar.
- **Sapma trendi:** Yıllık görsel rapor — "Bu sayaç son 12 ayda %1.2 sapma yapmış."
- **Bakım planlayıcı:** Periyodik kalibrasyon planı F8a Maintenance Schedule modülüyle takip.
- **Pilot için:** Torunlar GYO sahasında 30+ enerji ölçer var; ana giriş + chiller + 5 büyük AHU + aydınlatma. Yıllık kalibrasyon programı pilot sözleşmesinde tanımlı.

## Operatöre özet

Enerji ölçer bakımı operatörün değil, akredite kalibrasyon firmasının işidir. Operatör sadece sapma belirtilerini takip eder ve şüpheli durumda servis çağırır. Custos otomatik anomali tespit (örn. iki paralel AHU'nun farklı güç okuması) bu süreci hızlandırır.
