---
title: "Cooling tower approach sıcaklığı yüksek"
category: ariza
asset_template: cooling_tower
tags: [cooling_tower, approach, kireç, fan, dolgu]
versiyon: 1.0
yazar: Göktürk
tarih: 2026-04-28
kaynak: ASHRAE Handbook (Equipment, Cooling Towers) + CTI (Cooling Technology Institute) STD-201
---

# Soğutma kulesi yaklaşım (approach) sıcaklığı yüksek

Approach = supply suyu sıcaklığı − dış havanın yaş termometre (wet-bulb) sıcaklığı. Yeni cihazda 3-5 °C, sahanın aşınma ile 5-7 °C kabul edilir. 10 °C üstü performans kaybı işaretidir; chiller verimine doğrudan yansır (her 1 °C approach artışı chiller COP'sini ~%1.5-2 düşürür).

## Niçin önemli

Approach, kulenin termodinamik işini ne kadar iyi yaptığını gösteren tek temel metriktir. Range (return − supply) kapasiteye, approach kalitesine bağlıdır. Yüksek approach → chiller daha sıcak su ile besleniyor → kondenser basıncı artıyor → kompresör fazla iş yapıyor → AVM elektrik faturası artıyor.

## Olası sebepler

- **Dolgu malzemesi tıkalı / kireçli:** En yaygın sebep. PVC fill üstünde kalsiyum karbonat birikimi yüzey alanını düşürür.
- **Su dağıtım nozzle'ları tıkalı:** Bazı bölgelerde su düşmüyor — soğutma alanı küçülüyor.
- **Fan yetersiz:** VFD frekansı düşmüş, pala açısı yanlış, motor termik trip oluyor.
- **Hava sirkülasyonu blokesi:** Kule girişi ya da çıkışı yakınında engel (yapı, başka kule, çatı çıkıntısı).
- **Damla tutucu (drift eliminator) tıkalı:** Hava akışını sınırlandırır.
- **Pompa debisi düşük veya bypass açık:** Su akışı çoğunlukla approach'u etkilemez ama mekanizma değişir.
- **Drift kaybı çok yüksek:** Konsantrasyon artıyor, kireç hızla biriktiyor.

## Kontrol adımları

1. Custos `tower_supply_temp`, `tower_return_temp`, `wet_bulb_outdoor` (varsa) son 30 gün.
2. Range ve approach hesabı — geçmişe göre değişim?
3. Fan akımı / frekans / ses kontrol; tüm fanlar çalışıyor mu (multi-cell kulede)?
4. Kule üst kapağından görsel: dolgu üstünde kireç tabakası var mı? Su dağılımı homojen mi?
5. Nozzle'larda tıkanıklık olduğunu bazı bölgelerin kuru olmasından anlarsınız.
6. İletkenlik ölçer + son blowdown kaydı — biriken kireç riski var mı?
7. Service raporlarında son fill temizliği veya değişim tarihi?

## Kısa vadeli aksiyon

- Dolgu kapağını kapalı tutarak fan frekansını kademeli artır (VFD'liyse).
- Su debisini biraz artır (pompa frekansı varsa).
- Acil iş için chiller setpoint'i yumuşak yükselt; AVM dış sıcaklığa göre kabul edilir konfor sürdürülür.

## Kalıcı çözüm

- **Fill temizliği veya değişimi:** PVC fill 5-10 yılda bir değişir. Kireçlenmiş fill mekanik yıkama veya değişim. Maliyet 50-200 bin TL kule başına.
- **Su şartlandırma programı:** Otomatik blowdown + biyosit + scale inhibitor + dispersan dozaj sistemi.
- **Periyodik nozzle / dağıtım kontrolü:** Yılda 2 kez (yaz öncesi + sonrası).
- **Fan revizyonu:** Yağlama, balans, motor sargı testleri yılda 1.

## Pilot için Custos rolü

Custos `tower_supply_temp` + dış termometre + wet-bulb hesap (psychrometric) ile approach trendini günlük olarak kayıt eder. Trend artışı 3 °C → bakım önerisi otomatik açılır. Bu fonksiyon pilot kurulumun (Torunlar GYO) ek talep listesinde — şu anda manuel hesap.

## Operatöre özet

Approach yıllık trendi yapı bakım performansının özetidir. Yıllık ortalama 1 °C artış kabul edilir; daha hızlı artış aksiyon ister. AVM yöneticisine yıllık servis raporu Custos'tan otomatik dökümle sunulabilir.
