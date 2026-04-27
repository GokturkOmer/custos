---
title: "AHU supply temp setpoint sapması troubleshooting"
category: ariza
asset_template: ahu
tags: [ahu, sicaklik, setpoint, vana, kontrol, batarya]
versiyon: 1.0
yazar: Göktürk
tarih: 2026-04-28
kaynak: ASHRAE Handbook (HVAC Applications) + Belimo control valve technical guide
---

# AHU supply temp setpoint sapması

AHU üfleme havası setpoint'inden ±1.5 °C üstünde sapıyorsa veya saatlerdir setpoint'i yakalayamıyorsa kontrol problemi vardır. AVM'de bu durum mağaza şikayetine, müşteri konforu kaybına ve enerji israfına dönüşür.

## Belirti

- Custos `ahu_supply_temp` tag'ı setpoint çevresinde dalgalanıyor (osilasyon).
- Veya tek yönde sapıyor: hep yüksek (yetersiz soğutma) ya da hep düşük (aşırı soğutma).
- Soğutma vana açıklığı sürekli %100 veya sürekli %0.
- Bölgede konfor şikayeti.

## Olası sebepler

### A) Supply temp hep YÜKSEK (yetersiz soğutma)

- Soğutma vanası yeterince açılamıyor — aktüatör arızalı veya kontrolör çıkışı problemli.
- Chiller'dan gelen su sıcak (chiller setpoint kayması veya chiller arıza).
- Soğutma bataryası kireçli / hava sıkışmış.
- Filtre çok kirli, hava debisi düşük → batarya yetersiz soğutuyor.
- Setpoint çok düşük (örn. 14 °C, gerçekçi 16 °C).
- Dış hava damperi hatalı tam açılmış, sıcak dış hava karışıma fazla giriyor.

### B) Supply temp hep DÜŞÜK (aşırı soğutma)

- Soğutma vana sızıntısı; %0 komut verilmesine rağmen vana tam kapanmıyor.
- Donma koruması (frost) yanlış aktif kalmış.
- Setpoint çok yüksek olarak ayarlandı ama gerçekleşen sıcaklık donanım nedeniyle düşük kalıyor.

### C) Supply temp OSİLASYON yapıyor

- PID kontrol parametreleri yanlış (P çok yüksek veya I çok kısa).
- Sensör konumu yanlış — vana çıkışına çok yakın yerleştirilmiş.
- Sensör hatalı; trend gerçek sıcaklıktan farklı.
- Vana sticking: hareket ediyor ama ani sıçramalarla.

## Kontrol adımları

1. Custos `ahu_supply_temp` ve `ahu_cooling_valve_pos` grafiklerini paralel incele. Vana tepkisi mantıklı mı?
2. Filtre ΔP normal mi? (Düşük debi vana açıklığını yanıltır.)
3. Chiller gidiş suyu setpoint'i ve gerçek sıcaklığı oku — kaynak sağlam mı?
4. Sensörü kalibre kontrol yap (üretici prosedürü) — tipik 5 dakikada.
5. Vana aktüatör manuel komut ile %100 → %0 cevap verme süresi kontrol.
6. Kontrolör PID parametreleri son ne zaman değişti? Loglara bak.

## Kısa vadeli aksiyon

- Setpoint geçici olarak +1 °C yükselt (operasyon devam, alarm sıklığı azalır).
- Vana sticking şüphesi varsa manuel olarak birkaç tam kurs hareketi (servis + AVM yönetim onayı).
- Filtre durumu varsa öncelikle değiştir — dolaylı çözüm olabilir.

## Kalıcı çözüm

- Vana / aktüatör değişim veya servis (üretici prosedürü).
- Sensör değişim ve doğru konuma yerleştirme (üfleme kanalında 1-2 m mesafede).
- PID re-tune (autotune varsa, manuel ayar otomasyon mühendisi tarafından).
- Pilot için Custos `assistant_routes` üzerinden bu sapma yıllık raporda görülür; trend takibi önemli.

## Operatöre özet

Setpoint sapması tek başına alarm değil — saatlerce devam eden sapma alarmdır. Custos eşiği bunu otomatik ayırır. Acil müdahale gerekiyorsa setpoint geçici yükseltme + servis çağrısı sıralaması yeterli; cihazı durdurmak konfor kaybına neden olur.
