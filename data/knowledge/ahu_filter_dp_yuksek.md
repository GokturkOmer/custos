---
title: "AHU filtre ΔP (basınç farkı) yüksek alarmı"
category: ariza
asset_template: ahu
tags: [ahu, filtre, basinc, dp, alarm, hava_kalitesi]
versiyon: 1.0
yazar: Göktürk
tarih: 2026-04-28
kaynak: ASHRAE Handbook (HVAC Applications, IAQ) + Camfil filter best practice
---

# AHU filtre ΔP yüksek alarmı

Filtrenin önü ile arkası arasındaki basınç farkı (ΔP, delta-P) filtre kirlendikçe doğrusal olarak artar. AHU ΔP transmitter'ı veya manyetik anahtar bu değeri ölçer. Kalibrasyon eşiği aşıldığında alarm tetiklenir. AVM'de bu alarm haftalık-aylık aralıkta tekrar eden, ihmal edilirse hava kalitesini düşüren önemli bir bakım sinyalidir.

## Belirti

- Custos ekranında `filter_dp_supply` veya `filter_dp_main` tag'ı eşik değerin üstüne çıktı.
- Bölgede üfleme zayıflamış (operatör veya mağaza yorumu).
- Fan akımı normalden yüksek (sistem aynı debi için daha çok itme uyguluyor).
- Supply temp setpoint'i bazen yakalanamıyor — hava akışı düştüğü için batarya yeterince soğutamıyor.

## Olası sebepler

- **Filtre dolmuş — periyodik değişim zamanı:** En yaygın sebep. Pre-filter ortalama 4-12 hafta, main filter 3-6 ay AVM saha şartlarında.
- **Anormal toz yükü:** Yakın inşaat, hava döngüsü değişikliği, dış hava damperi tam açılmış (CO₂ talimatı).
- **Filtre yanlış takılmış:** Conta kaçırması veya çerçeveye oturmamış filtre. Hava bypass yapıyor, ΔP göstergesi yanıltıcı yüksek.
- **Birden çok filtre kademesinde aynı anda kirlilik:** Sayım hatası, sadece pre-filter değişim.
- **ΔP transmitter / sensor hatası:** Tıkanmış kılcal, kalibre kayması, bozuk transmitter — gerçek değerden çok yüksek okuyor.
- **Fan VFD'si yüksek frekansta:** Arttırılmış debi → ΔP doğal olarak artar; alarm setpoint güncellenmemiş.

## Kontrol adımları

1. Custos `filter_dp_main` grafiğini son 30-90 gün incele — yavaş artış mı yoksa ani sıçrama mı?
2. AHU önünde panel ölçer veya manometre ile ΔP'yi fiziksel oku; transmitter ile kıyasla.
3. Filtre erişim kapağını aç (ekipman durmuş veya bypass modunda) ve görsel kontrol.
4. Ön filtre çok kirli ama ana filtre temizse, ön filtre değişimi yeterli olabilir.
5. ΔP gerçekten yüksek değilse transmitter / hortum / kılcal kontrol.

## Kısa vadeli aksiyon

- Filtre değişim takvimine acil ek; 24-48 saat içinde değişim.
- Ekipman kapatılmaz — düşük performansla çalışmaya devam (insan sağlığı için filtre dahi kirli olsa hava akışı tutulur).
- AVM yöneticisi bilgilendirilir, mağaza şikayeti gelirse hızlı dönüş için filtre stoğu hazır olmalı.

## Kalıcı çözüm

- **Periyodik takvim:** Pre-filter 8 hafta, main filter 4 ay ortalama; AVM saha tozu yüksekse 6 ve 3 ay.
- **Stok yönetimi:** Pilot kurulumda 2 set yedek filtre stoğu (Custos satış paketinden ayrı).
- **Eşik kalibre:** Filtre değişim sonrası baseline ΔP yeniden kayıt; alarm eşiği baseline + 80-100% olarak güncelle.
- **Kayıt:** Custos bakım sayfasında her değişim tarih + filter tipi + servis personeli kaydedilir; trend desteği COP / fan akımı analizine girer.

## Operatöre özet

ΔP alarmı kötü haber değil — filtrenin işini yaptığını gösterir. Doğru aksiyon: zamanında değişim. Geciktirilmiş filtre değişimi fan elektrik tüketimini %5-15 artırır ve hava kalitesini düşürür.
