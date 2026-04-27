---
title: "Sirkülasyon pompası titreşim arızası"
category: ariza
asset_template: circulation_pump
tags: [sirkulasyon, pompa, titresim, bearing, balans]
versiyon: 1.0
yazar: Göktürk
tarih: 2026-04-28
kaynak: ISO 10816-3 + Wilo + Grundfos titreşim servis dokümanları
---

# Sirkülasyon pompası titreşim arızası

Pompa titreşimi yıpranma + arıza işaretidir. ISO 10816-3 sınıfına göre AVM HVAC pompası 1.4-2.8 mm/s rms üstü "alert" (uyarı), 4.5-7.1 mm/s üstü "danger" (acil). Custos `pump_vibration_rms` tag'ı bu eşiklere göre alarm üretir.

## Belirti

- Custos titreşim tag'ı eşik aşmış.
- Pompa odasında veya pompanın kendisinde belirgin gürültü.
- Hat boruları sallanıyor, bağlantı vidaları kendiliğinden gevşiyor.
- Salmastra sızıntısı artmış (titreşim contayı yıpratır).
- Motor sıcaklık yüksek (titreşim → bearing → ısı).

## Olası sebepler

### A) Mekanik

- **Bearing arızalı:** En yaygın. Balls / rollers yıpranmış, yağlama yetersiz, kontaminasyon.
- **Çark dengesizliği:** Çark üzerinde malzeme birikimi, çark kanat kırılması.
- **Mil eğilmiş:** Servis sırasında darbe, montaj hatası.
- **Bağlantı kapling yıpranmış:** Pompa-motor arası flexible coupling.
- **Temel cıvataları gevşek:** Uzun çalışma + termal genleşme.

### B) Hidrolik

- **Kavitasyon:** Düşük emiş basıncı, hava sıkışma, NPSH yetersiz.
- **Akış darbeleri:** Çekvalf ani kapanma, vana hızlı kapanma.
- **Hat içinde hava:** Sistem havalandırılmamış.
- **Pompa BEP (Best Efficiency Point) dışı çalışıyor:** Çok düşük veya çok yüksek debi.

### C) Elektriksel

- **Faz kaybı:** Tek fazda iş yapan pompa zıplar.
- **VFD harmonik:** Yüksek harmonik motor titreşim üretir.
- **Motor stator arızası:** Sargı kısa devre ön belirtisi.

## Kontrol adımları

1. Custos `pump_vibration_rms` grafiği son 90 gün — yavaş artış mı, ani sıçrama mı?
2. Üç eksen titreşim ölçer (yatay X, yatay Y, dikey Z) ile manuel ölçüm — frekans spektrumu çıkarılırsa arıza türü ayırt edilir (1× RPM = balanssızlık, 2× = mil eğilim, bearing freq = bearing arıza).
3. Görsel: pompa gövdesi temas, çıt sesi var mı? Bağlantı cıvataları torque kontrolü.
4. Salmastra sızıntı miktarı (damla / dakika).
5. Motor sıcaklık (IR termal kamera veya kontak sensör).
6. Hidrolik kontrol: sistem havalandırma, NPSH durum, vana pozisyonları.

## Kısa vadeli aksiyon

- Titreşim "alert" seviyesinde — operasyon devam ama servis 1-2 hafta içinde planlama.
- Titreşim "danger" seviyesinde — pompa bypass + ikinci pompaya devir + acil servis.
- Hat içinde hava şüphesi varsa havalandırma vanası açma.

## Kalıcı çözüm

- **Bearing değişim:** AVM kullanımında 4-7 yılda bir tipik. Custos runtime hours'ı izler.
- **Çark balansı:** Servis sırasında balans tezgahında 6.3 mm/s veya daha düşük.
- **Salmastra değişim:** Bearing değişimi ile birlikte (pompa söküldüğünde).
- **Kapling:** 3-5 yılda bir yenileme.
- **Temel kontrolü:** Vibration analiz isolaters kullanımı; AVM çelik konstrüksiyonda anti-vibration mount şart.

## Pilot için Custos rolü

Custos titreşim trendini günlük olarak baseline ile karşılaştırır. %20 üstü artış erken uyarı; %50 üstü artış servis çağrı tetikleme. Pilot kurulumunda Torunlar GYO HVAC servisi ile **predictive maintenance** ortak çalışma — 6 ay sonra titreşim verileriyle bearing değişim önerileri.

## Operatöre özet

Titreşim "yapışkan" bir arızadır — yavaş artar, sonra hızla bozulur. Custos eşiğin altı normal, üstü servis çağırma anlamı taşır. Ses ve görsel kontrol haftalık kontroller sırasında; titreşim ölçümü aylık servis kapsamında.
