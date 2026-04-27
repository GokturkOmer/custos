---
title: "Cooling Tower (Soğutma Kulesi) çalışma prensibi"
category: ekipman
asset_template: cooling_tower
tags: [cooling_tower, kule, evaporative, fan, kondenser_su]
versiyon: 1.0
yazar: Göktürk
tarih: 2026-04-28
kaynak: ASHRAE Handbook (HVAC Systems & Equipment, Bölüm 40) + BAC, Marley servis kılavuzları
---

# Cooling Tower (Soğutma Kulesi) çalışma prensibi

Soğutma kulesi, su soğutmalı çillerlerin kondenser tarafındaki ısıyı atmosfere atan ekipmandır. Çalışma prensibi evaporatif soğutma — sıcak suyun bir kısmı buharlaşırken alttan yukarı doğru üflenen havayla ısı transferi yapar. AVM merkezi soğutma sisteminde 1-3 kule yaygındır, her biri 200-1500 kW soğutma kapasitesi atar.

## Temel bileşenler

- **Sıcak su girişi (basın boruları):** Chiller'dan dönen sıcak kondenser suyu üst kısımdaki dağıtım sistemine basılır.
- **Su dağıtım sistemi (spray nozzle / distribution deck):** Suyu kule kesiti boyunca homojen dağıtır.
- **Dolgu malzemesi (fill / film fill):** Plastik veya tahta karbon yapı; suyun yüzey alanını maksimize eder. Hava-su temas noktası burası.
- **Damla tutucu (drift eliminator):** Üst tarafta — buharla birlikte sürüklenen su damlalarını (drift) tutar, çevreye yayılmasını önler.
- **Fan:** Üst tarafta (tepe-fanlı / induced draft) veya alt tarafta (zorlanmış / forced draft). AVM'de 5.5-30 kW fan motoru yaygın.
- **Su havuzu (basin):** Soğuyan su altta toplanır, kondenser pompa ile çillere geri basılır.
- **Make-up water valve:** Buharlaşma + drift kayıpları için temiz su girişi.
- **Blowdown:** Kireç birikimini önlemek için dipten dışarı su atımı (kontrollü).

## Çalışma mantığı

1. Chiller kondenserinden sıcak su (35-40 °C) kuleye basılır.
2. Üst dağıtım sistemi suyu kule içine yayar.
3. Fan altta veya tepede hava akışı oluşturur.
4. Su yüzeylerinde buharlaşma → ısı havaya geçer.
5. Soğuyan su (28-32 °C) altta toplanır.
6. Kondenser pompa bu suyu chiller'a geri basar.

Wet-bulb sıcaklığı (yaş termometre) teorik soğutma sınırıdır; kule supply'sı wet-bulb +3-5 °C üstüne inebilir (approach).

## Kritik parametreler ve normal aralıklar

- **Kule supply (kondenser supply):** 28-32 °C — chiller bu sıcaklıkla beslenir.
- **Kule return (kondenser return):** 35-40 °C — chiller'dan döner.
- **Approach:** Supply - wet-bulb = 3-7 °C yeni cihazda. 10 °C üstü problem işareti.
- **Range:** Return - supply = 5-7 °C tipik.
- **Fan akımı:** Etiket FLA'nın %60-90'ı.
- **Su seviyesi (basin level):** Üretici belirlediği aralık; düşük ise make-up valve arızası.
- **İletkenlik (conductivity):** Kireçlenme indikatörü — 1500-2500 µS/cm hedef; üstü blowdown gerekir.
- **pH:** 7-8.5 hedef; düşük pH korozyon, yüksek pH kireç.

## Sık karşılaşılan alarmlar / problemler

- **Approach yüksek:** Dolgu malzemesi tıkalı, su dağıtımı düzensiz, fan yetersiz.
- **Supply temp yüksek:** Approach + range ikisi birden artmış; kapasite yetersiz.
- **Su seviyesi düşük:** Make-up valve arızası veya su kaynağı kesik.
- **Fan trip:** Termik veya mekanik arıza.
- **Kireç birikimi:** İletkenlik kontrol cihazı veya periyodik blowdown ayarsız.
- **Biyolojik büyüme (Legionella riski):** Yetersiz biyosit dozajı, ısı + su = bakteri için ideal.

## Operatöre kısa rehber

Soğutma kulesi yaz aylarında AVM enerji performansının kritik halkasıdır. Haftalık kontroller: su seviyesi, fan ses + titreşim, dağıtım nozzle'ları görsel. Aylık: iletkenlik + pH ölçümü, biyosit dozajı.

**Legionella riski:** Soğutma kuleleri Legionella üremesi için risk noktasıdır (ABD'de zorunlu rapor, Türkiye'de Sağlık Bakanlığı önerisi). AVM operatörü mutlaka periyodik mikrobiyolojik analiz ve kayıt tutmalıdır. Custos bu kaydı bakım sayfasında destekler.
