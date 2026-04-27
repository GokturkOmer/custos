---
title: "FCU (Fan Coil Unit) çalışma prensibi"
category: ekipman
asset_template: fcu
tags: [fcu, fan, batarya, oda, klima]
versiyon: 1.0
yazar: Göktürk
tarih: 2026-04-28
kaynak: ASHRAE Handbook (Systems & Equipment, Bölüm 5) + Carrier 42 Series technical
---

# FCU (Fan Coil Unit) çalışma prensibi

FCU, bir oda veya küçük zone (genelde 25-100 m²) için yerelde havayı şartlandıran küçük cihazdır. AHU'dan farklı olarak taze hava işlemi yapmaz, sadece bölgenin kendi havasını sirküle eder. AVM'de tipik kullanım: yönetim ofisleri, küçük mağaza arka oda, soyunma odası, sinema teknik odası. Bir AVM'de 50-300 FCU yaygındır.

## Temel bileşenler

- **Fan:** 3-hızlı (low / med / high) veya VFD'li EC motor.
- **Soğutma + ısıtma bataryası:** İki borulu (sadece soğutma veya sadece ısıtma) veya dört borulu (eş zamanlı ikisi). AVM'de iki borulu sezonluk değişen yaygın.
- **Hava filtresi:** Pre-filter G3-G4. Genelde tek kademe; daha kaliteli hava istenen ofiste F7 olabilir.
- **Termostat:** Oda termostatı veya merkezi BMS bağlantısı.
- **Vana(lar):** 2-yollu veya 3-yollu motorlu vana. On/off veya modüle.
- **Kondens tahliye:** Soğutma sırasında nemi alır, kondens tablası + drenaj boru.

## Hava akışı

1. Oda içinden hava dönüş ızgarasından çekilir.
2. Hava filtresi geçer.
3. Mevsime göre soğutma veya ısıtma bataryasından geçer.
4. Fan basıncıyla supply ızgarasına üflenir.
5. Oda hacmindeki hava dolaşır, doğal yayılım + termik konvektif harekat ile yeniden dönüşe ulaşır.

FCU 5-10 dakikada bir oda hacmini sirküle eder; bu nedenle setpoint'e yakın çalışırsa konfor stabil kalır.

## Kritik parametreler ve normal aralıklar

- **Oda termostat setpoint:** Yaz 23-25 °C, kış 21-23 °C. AVM yönetim ofisi 22 °C tipik.
- **Setpoint sapması:** Yerelde ±1 °C kabul; daha fazlası fan veya vana problemine işaret.
- **Fan akımı:** Etiket değerinin %60-90'ı; 3-hızlı fanda hız kademesi bilinmeli.
- **Soğutma vana açıklığı:** %0-%100; sürekli %100 + setpoint tutmuyor → kapasite veya kaynak yetersiz.
- **Kondens drenajı:** Sürekli akış yok ama soğutma sırasında damla. Tıkalı drenaj → kondens tabağı taşması → tavan zararı.

## Sık karşılaşılan alarmlar

- **Fan çalışmıyor:** Termik trip, motor arıza, kayış kopması (eski tip), kapasitör arızası (kondansatör tipi motor).
- **Soğutmuyor / ısıtmıyor:** Vana açılmamış (motor arızası veya kontrol sinyali yok), kaynak suyu sıcaklığı yanlış, hava sıkışması batarya içinde.
- **Kondens tabağı taşması:** Drenaj tıkalı veya eğim hatası. Tavan kaplaması zarar görür.
- **Filtre tıkalı:** Hava akışı çok düşük; oda setpoint'e ulaşmıyor.
- **Termostat / BMS bağlantı kopuk:** FCU varsayılan moda düşer (genelde duran).

## Operatöre kısa rehber

FCU bireysel olarak büyük arıza üretmez ama bir AVM'de 100+ FCU varsa periyodik bakım takvimi kritiktir. Custos asset_template = fcu instance'ları toplu izlenir; benzer setpoint sapması paterni birden fazla FCU'da varsa kaynak (chiller, pompa) tarafında problem olabilir. Tek FCU sapması yerel donanım problemi.
