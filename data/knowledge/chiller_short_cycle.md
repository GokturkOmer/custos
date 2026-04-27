---
title: "Chiller short-cycle (kompresör sık on/off) müdahalesi"
category: ariza
asset_template: chiller
tags: [chiller, kompresor, short-cycle, alarm, refrigerant]
versiyon: 1.0
yazar: Göktürk
tarih: 2026-04-28
kaynak: ASHRAE Handbook (HVAC Applications) + Carrier 30HX servis notları
---

# Chiller short-cycle (sık on/off)

Short-cycling, kompresörün dakikalar içinde defalarca devreye girip çıkması anlamına gelir. Saatte 6'dan fazla start = short-cycle eşiği (Carrier servis kılavuzu). Bu durum kompresör sargılarını ısıtır, yağ dolaşımını bozar, ekipman ömrünü ciddi şekilde kısaltır. Custos'ta `compressor_current` tag'ı dakikada 2-3 kez sıfıra dönüyorsa bu desen aktiftir.

## Belirtiler

- Kompresör akımı 2-5 dakika çalıştıktan sonra sıfıra düşüyor, ardından tekrar yükseliyor.
- Evaporator gidiş sıcaklığı setpoint civarında ama oturmuyor — sürekli yukarı/aşağı zıplıyor.
- Operatör panelinde "low load" veya "anti-cycle" zaman aşımı uyarısı.
- Ses olarak panel odasından sık başlatma sesi geliyor.

## Olası sebepler

- **Düşük proses yükü:** Bina soğutma talebi cihaz minimum kapasitesinin altında. AVM'de gece veya kapalı dönemde sık görülür. Cihaz 3 modülasyon kademesinin altına inemez.
- **Refrigerant şarj eksik veya fazla:** Sight glass'ta köpük (eksik) veya sıvı kabarcığı (fazla). Yetersiz şarj evaporator basıncını düşürür, LP trip → start tekrarı.
- **Kontrolör histeresis dar:** Setpoint ΔT bandı 1 °C'ten az ayarlanmışsa cihaz minimum kapasitede bile yükten fazla soğutma yapar. Bant genişlet (2-3 °C).
- **Evaporator scaling / kireçlenme:** Boru iç yüzeyinde kalsiyum karbonat tabakası ısı transferini düşürür; cihaz setpoint'e ulaşamaz, sürekli durup yeniden başlar.
- **Su debisi düşük:** Pompa kavitasyonu, tıkalı filtre, kapanmış vana — yetersiz akışta düşük basınç + flow trip.
- **Kondenser fanı arıza:** Yüksek basınç tripi sonrası restart döngüsü.

## Kontrol adımları

1. Custos `compressor_current` ve `evap_supply_temp` grafiklerini son 6 saat incele — başlatma desenini doğrula.
2. Üretici panelinden son 10 alarm kaydını oku; trip sebebi LP / HP / flow / oil hangi?
3. Sight glass'a bak (cihazda mevcutsa). Sıvı renk berrak değilse refrigerant problemi.
4. Pompa akımı ve fark basınç (ΔP) kontrolü — debi yeterli mi?
5. Setpoint bandını incele; ayarsız ise kontrolör menüsünden ΔT genişlet.
6. AVM yük profili: gece çalışıyorsa cihazı saatlik bypass moduna almak veya küçük çiller'a paralel iş yapısı kurmak gerekebilir.

## Kısa vadeli aksiyon

Setpoint bandını 1 °C → 2.5 °C'a genişlet. Anti-cycle timer'ı 5 dakikadan 10 dakikaya çıkar (üretici menüsü). Bu durum geçici rahatlama sağlar, kök neden çözülmez ama operasyonu sürdürür.

## Kalıcı çözüm

Kök sebebe göre: refrigerant şarj kontrolü + sızıntı tespiti, evaporator kimyasal temizliği, debi kontrolü ve pompa servisi. Yük problemi süreklilik gösteriyorsa **sequencing controller** veya küçük yardımcı chiller çözümü ön plana alınır. Tüm değişiklikler Custos `versiyon` etiketi ile bakım kayıt sayfasına notlanır.
