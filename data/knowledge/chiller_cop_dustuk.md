---
title: "Chiller COP (verim) düşmesi nedenleri"
category: ariza
asset_template: chiller
tags: [chiller, cop, verim, kondenser, evaporator, refrigerant]
versiyon: 1.0
yazar: Göktürk
tarih: 2026-04-28
kaynak: ASHRAE Handbook (Equipment, COP measurement) + Daikin servis notları
---

# Chiller COP düşmesi

COP (Coefficient of Performance), birim elektrik tüketimi başına üretilen soğutma kapasitesidir: COP = soğutma_kW / elektrik_kW. Yeni kuruluşta tipik COP 5-6, saha şartlarında 3.5-5 normal. COP zamanla %10-30 düşebilir; bu da AVM elektrik faturasında yıllık %5-15 fark anlamına gelir. Custos, kompresör akımı + soğutma yükü tag'larını birleştirerek operasyonel COP yaklaşıkını hesaplar; trend aşağı yönlüyse uyarı eşiği aşılır.

## Belirti

- Aynı kullanım profilinde elektrik tüketimi geçmiş aylara göre %10+ artmış.
- Aynı setpoint ve aynı dış sıcaklıkta kompresör akımı yüksek, gidiş sıcaklığı setpoint'e zorlukla iniyor.
- Custos `cop_estimated` veya `compressor_kW_per_TR` grafiği son 30-90 günlük dönemde belirgin yükseliyor.

## Olası sebepler

- **Kondenser kirli veya kireçli:** Hava soğutmalıda toz/kuş tüyü; su soğutmalıda kireç. Isı atımı zorlaştığı için kompresör daha yüksek deşarj basıncında çalışır → akım artar.
- **Evaporator scaling:** Aynı mantık ters yönden; ısı çekme zorlaşır, kompresör daha düşük emişte çalışır → debi başına daha çok iş.
- **Refrigerant azalmış (slow leak):** Şarj %5-10 düşüş bile COP'yi ciddi bozar.
- **Kontamine refrigerant / yağ:** Nem, asit, partikül. Filter-drier doymuş.
- **Yıpranmış kompresör:** İç sıkışma kayıpları artmış. Servis raporlarında "blow-by" yüksek.
- **Yanlış setpoint:** Setpoint gereksiz düşük (örn. 5 °C yerine 7 °C yetiyorsa). Her 1 °C aşağı setpoint COP'yi %2-3 düşürür.
- **Kondenser fanı eksik / yavaş:** VFD frekansı düşürülmüş veya bir fan trip durumda.

## Kontrol adımları

1. Custos overview chart'ta `compressor_kW`, `evap_supply_temp`, `condenser_supply_temp` son 90 gün karşılaştır.
2. Dış sıcaklık trendini Custos NTP log + dış termostat tag'ı ile karşılaştır — kıyaslama hava şartlarına dengeli olmalı.
3. Servis kayıtlarında son kondenser temizlik tarihi 6 aydan eski mi?
4. Refrigerant son şarj veya leak-test tarihi 12 aydan eski mi?
5. Setpoint optimizasyon: AVM kullanım dilimlerinde 1 °C yükseltme deneme.
6. VFD'li cihazsa fan veya pompa frekansları hep maksimumda mı?

## Kısa vadeli aksiyon

- Görsel: kondenser temizlik durumu fotoğrafla.
- Setpoint 1 °C yükseltme (örn. 6 → 7) deneme; AVM iç sıcaklık bozulmuyorsa kalıcılaştır.
- Kondenser fan VFD frekansını üretici minimum ile maksimum arasında optimize et.

## Kalıcı çözüm

- Kondenser kimyasal temizlik (yılda 1 kez yaz öncesi).
- Evaporator chemical cleaning (3-5 yılda 1).
- Refrigerant leak test + recharge (gerekirse).
- COP iyileştirmesi sonrası baseline yeniden çıkar; Custos eşik değerleri (`anomaly_score_threshold`) güncellenir.

## Pilot için not

AVM pilotunda (Torunlar GYO) chiller için COP baselining ilk 2-3 hafta sürer. Bu sürede `cop_estimated` tag'ı sadece gözlem için tutulur, alarm tetiklemez.
