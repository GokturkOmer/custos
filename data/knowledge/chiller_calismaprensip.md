---
title: "Chiller çalışma prensibi"
category: ekipman
asset_template: chiller
tags: [chiller, kompresor, evaporator, kondenser, sogutma]
versiyon: 1.0
yazar: Göktürk
tarih: 2026-04-28
kaynak: ASHRAE Handbook (Systems & Equipment, Bölüm 43) + Trane CGAM/CGWN servis kılavuzu
---

# Chiller çalışma prensibi

Chiller (soğutucu grup), bir AVM HVAC sisteminde merkezi soğutma kaynağıdır. Termodinamik olarak basit bir mekanik soğutma çevrimi (vapor compression cycle) çalıştırır: bir tarafta proses suyundan ısı çekerek soğutur, diğer tarafta bu ısıyı kondenser üstünden ortama veya kuleye atar. AVM'de tipik kapasite 200-1500 kW soğutma yüküdür.

## Temel bileşenler

- **Kompresör** — Soğutucu akışkanı (refrigerant) emerek basıncını ve sıcaklığını yükseltir. AVM'de yaygın tipler: scroll (küçük kapasite, 50-300 kW), screw (orta-büyük kapasite, 300-1500 kW), centrifugal (>1000 kW, daha çok ofis kuleleri).
- **Kondenser** — Yüksek basınçlı kızgın gazın ısısını dış ortama veya kondenser suyuna verdiği eşanjör. Hava soğutmalı (fanlı) veya su soğutmalı (cooling tower'a bağlı) olabilir.
- **Genleşme valfi (TXV / EEV)** — Sıvılaşmış refrigerant'ın basıncını düşürür; çıkışta düşük sıcaklık + düşük basınçta soğuk akışkan olur.
- **Evaporator** — Düşük basınçlı refrigerant proses suyundan ısı çeker, buharlaşır. Çıkıştaki soğuk su (genelde 6-7 °C) santrallere ve fan-coil ünitelerine gönderilir.
- **Kontrol paneli** — Setpoint kontrolü, alarm yönetimi, kapasite modülasyonu (slide valve / VFD), Modbus arayüzü.

## Çalışma çevrimi

1. Evaporator: refrigerant düşük basınçta proses suyundan ısı alarak buharlaşır.
2. Kompresör: bu buharı emer, basıncını 10-15 bar mertebesine, sıcaklığını 70-100 °C'ye çıkarır.
3. Kondenser: kızgın gaz dış havaya veya su kuleye ısı verir, sıvılaşır.
4. Genleşme valfi: sıvı refrigerant kısıtlanır, basıncı 3-5 bar mertebesine düşer, sıcaklığı 0-5 °C civarına iner.
5. Çevrim baştan başlar.

Sürekli rejimde evaporator gidiş-dönüş arasındaki sıcaklık farkı (ΔT) kapasiteyi belirler — tipik ΔT 5 °C'dir.

## Kritik parametreler ve normal aralıklar

- **Evaporator gidiş suyu sıcaklığı (LCHWT):** 6-7 °C setpoint, ±0.5 °C tolerans.
- **Evaporator dönüş suyu sıcaklığı:** 11-13 °C arası, ΔT 5-6 °C.
- **Kompresör akımı:** Etiket FLA değerinin %40-90'ı; %95 üstü kapasiteyi sınırlandırır.
- **Deşarj basıncı (HP):** R-134a için 9-13 bar, R-410A için 22-32 bar (kondenser tipine göre).
- **Emiş basıncı (LP):** R-134a için 3-4 bar, R-410A için 7-9 bar.
- **Yağ basıncı:** Üreticinin alt sınırı +1.5-2 bar üstü; çoğu cihaz 1.5 bar altında kompresörü trip eder.
- **COP (operasyonel verim):** Yeni cihazda 5-6, AVM saha şartlarında 3.5-5 sağlıklıdır.

## Sık karşılaşılan alarmlar

- **Yüksek deşarj basıncı (HP trip):** Kondenser fanı veya yüzey sorunları.
- **Düşük emiş basıncı (LP trip):** Refrigerant kayıp, evaporator scaling, düşük su debisi.
- **Yağ basınç düşük:** Yağ kirlenmesi, soğuk start, kompresör arızası ön belirtisi.
- **Akış (flow) yetersiz:** Pompa devre dışı, vana kapalı, kirli filtre.
- **Donma alarmı:** Evaporator çıkışı 2 °C altına düştüğünde — çoğunlukla düşük su debisi.

## Operatöre kısa rehber

Chiller alarm verdiğinde önce **alarm kodunu ve kategorisini Custos alarmlar sayfasında oku**. Cihazı panel üstünden kapatmak yerine SCADA üstünden kontrollü duruşa geç. Custos hiçbir zaman cihaza yazma yapmaz; tüm aksiyon insan operatör + SCADA üstündendir. Şüpheli durumda bakım servisi çağrılır, panel kapanış logu fotoğraflanır.
