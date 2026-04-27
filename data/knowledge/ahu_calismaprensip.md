---
title: "AHU (Air Handling Unit / Klima Santrali) çalışma prensibi"
category: ekipman
asset_template: ahu
tags: [ahu, klima, santral, fan, bataryasi, filtre]
versiyon: 1.0
yazar: Göktürk
tarih: 2026-04-28
kaynak: ASHRAE Handbook (HVAC Systems & Equipment, Bölüm 4) + Trane Climate Changer servis kılavuzu
---

# AHU (Klima Santrali) çalışma prensibi

AHU (Air Handling Unit / Türkçe Klima Santrali), bir AVM içinde belirli bir bölgenin (mağaza, koridor, food court, sinema) havasını şartlandıran orta büyüklükte cihazdır. Tipik AVM'de 5-30 AHU bulunur, kapasiteleri 5,000-50,000 m³/h debi aralığındadır. AHU; havayı filtreler, ısıtır/soğutur, nemini ayarlar, gerekirse karıştırır ve bölgeye basınçlı şekilde gönderir.

## Temel bileşenler

- **Karışım odası (mixing box):** Dış hava (fresh air) damperi + dönüş hava (return) damperi. CO₂ veya zaman programıyla oran ayarlanır.
- **Ön filtre (pre-filter):** Kaba filtre (G3-G4 / ePM10 50%). Toz, polen, böcek tutar.
- **Ana filtre:** İnce filtre (F7-F9 / ePM2.5 70-90%). Daha küçük partiküller. AVM food court için F7 yaygın.
- **Soğutma bataryası:** Chilled water beslemeli; soğuk su ile hava soğutulur ve nemi alınır.
- **Isıtma bataryası:** Sıcak su veya elektrik. Kış için ısıtma + soğutmayla ön ısıtma kombinasyonu.
- **Nemlendirici (opsiyonel):** Buharlı veya adyabatik. AVM'de sinema ve kuyumcular için kritik.
- **Supply fan:** Hava akımını oluşturur. AC veya VFD'li EC motor.
- **Return fan:** Bölgeden havayı geri çeker (büyük AHU'larda).
- **Damperler:** Yangın, motorlu, by-pass.

## Hava akışı

1. Bölgeden gelen dönüş havası karışım odasına ulaşır.
2. Dış hava damperinden gelen taze hava ile karışır.
3. Pre-filter + main filter sırayla geçilir.
4. Mevsime göre soğutma veya ısıtma bataryasından geçer.
5. Nem ayarı yapılır (varsa nemlendirici).
6. Supply fan basınçla bölgeye gönderir.
7. Bölgeden geri dönüş hava kanallarından AHU'ya döner — döngü kapanır.

## Kritik parametreler ve normal aralıklar

- **Supply temp setpoint:** 14-18 °C soğutma modunda, 26-32 °C ısıtma modunda.
- **Setpoint sapma:** ±1.5 °C tolerans; daha fazlası kontrol problemine işaret.
- **Filtre ΔP (basınç farkı):** Pre-filter 50-150 Pa, main filter 100-250 Pa kalibrasyon değerinden orijinaldir. 1.5x üstü sınır, 2x üstü zorunlu değişim.
- **Fan akımı:** Etiket FLA'nın %60-90'ı tipik; %95 üstü VFD frekansı kontrolü.
- **Soğutma vana açıklığı (%):** %5-90 arası operasyonel; sürekli %100 → kapasite yetersiz.
- **CO₂ (varsa):** 800 ppm altı sağlıklı, 1000 ppm üstü taze hava artırma sinyali.
- **Bölge basıncı:** AVM ortak alanı tipik +5 ile +15 Pa pozitif (dış havadan içeriye); food court bağımsız kontrol.

## Sık karşılaşılan alarmlar

- **Filtre ΔP yüksek:** Filtre kirli, değişim gerek.
- **Supply temp setpoint sapması:** Vana, batarya, fan veya kontrol problemi.
- **Fan trip:** Termik koruma, kayış kopması, motor arızası.
- **Donma alarmı (frost protect):** Soğutma bataryasında 2 °C altı; kış pozisyonu hatası.
- **Yangın damperi tetiklendi:** İtfaiye sistemi sinyali — AHU otomatik durdurma.

## Operatöre kısa rehber

AHU haftalık görsel kontrol: filtre durumu, kayış gergisi, kondens su tahliyesi. Aylık: vana çalışma, damper hareketi, sensör temizliği. AVM'de kullanım yoğunluğu yüksek olduğu için filtre ömrü genelde tasarım değerinin %70-80'i; takvimi sıkı tut.
