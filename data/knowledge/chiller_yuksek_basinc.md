---
title: "Chiller yüksek deşarj basıncı (HP) alarmı"
category: ariza
asset_template: chiller
tags: [chiller, basinc, alarm, kondenser, fan]
versiyon: 1.0
yazar: Göktürk
tarih: 2026-04-28
kaynak: ASHRAE Refrigeration Handbook + Trane CGAM servis kılavuzu
---

# Chiller yüksek deşarj basıncı

Kompresör deşarj tarafındaki basınç (high side / discharge / HP) üreticinin belirlediği eşiğin (R-134a için tipik 13-15 bar, R-410A için 32-35 bar) üstüne çıktığında bu alarm tetiklenir. Devam ederse HP switch kompresörü trip eder ve cihaz lockout'a düşer. AVM kondenser yaz aylarında yüksek dış sıcaklıkla birleşince en sık görülen alarmdır.

## Olası sebepler

- **Kondenser fanı çalışmıyor veya yavaş dönüyor:** Tek fan trip olmuş, VFD frekansı düşmüş, motor sigortası açmış.
- **Kondenser yüzeyi tozlu / tıkalı:** AVM çevresinde polen, yaprak, hava taşınımı tozu. Hava akışı %50 azaldığında HP %20-40 yükselir.
- **Ortam sıcaklığı mevsim normallerinin üstünde:** Hava soğutmalı çillerde 38 °C dış üstü kritik. Sıcak hava dalgalarında saatlik yük profili dışı çalışma.
- **Refrigerant fazla şarj edilmiş:** Yüksek deşarj basıncı + yüksek subcool. Sight glass'ta sıvı kabarcığı yok ama yüksek basınç uyarısı sürekli.
- **Su soğutmalı kondenser ise:** Cooling tower fanı arızalı, su sirkülasyon pompası düşük debi, tower water sıcaklığı normal aralık dışı.
- **Kondenser scaling (su tarafı):** Cooling tower devresi kireçli. Yıllık biriken kalsiyum tabakası ısı transferini düşürür.
- **Hava sirkülasyonu blokesi:** Kondenser yakınında engel (palet, çadır, başka klima üfleme).

## Kontrol adımları

1. Custos `condenser_supply_temp` ve `compressor_discharge_pressure` son 24 saat trendi — saat saat dış sıcaklıkla mı paralel?
2. Kondenser fanlarının (veya fan grubunun) tamamı çalışıyor mu? Görsel ve panel akım okuma.
3. Fan motor akımını üretici nominal değeri ile karşılaştır.
4. Kondenser yüzeyini görsel incele — kir, yaprak, hasar, tıkalı kanat.
5. Ortam sıcaklığını not al; servis raporundaki mevsim referansı ile karşılaştır.
6. Refrigerant sight-glass'ta köpüklenme / flaş var mı? (Köpük → eksik şarj; berrak ve sürekli kabarcık → fazla şarj.)
7. Su soğutmalı sistemde tower supply / return ΔT, tower fan akımı kontrol.

## Kısa vadeli aksiyon

- Kondenser yüzey görsel temizliği (basınçlı hava veya su, üretici kılavuzuna göre).
- Hava akışını engelleyen geçici nesne varsa derhal kaldır.
- Çok sıcak günde geçici olarak setpoint 1 °C yükseltmek HP'yi düşürür (kompresör daha az çalışır).
- Tek fan trip ise sigortayı kontrolden geçir; hata tekrarlıyorsa motor / kapasitör testi.

## Kalıcı çözüm

- **Periyodik kondenser temizliği:** Yaz öncesi nisan-mayıs ayında zorunlu, AVM çevre tozluluğuna göre 30/60/90 gün periyot.
- **Su tarafı:** Su şartlandırma kimyasalı, biyosit, periyodik kireç temizliği (yılda 1).
- **Refrigerant şarj kontrolü:** Üretici prosedürüyle yıllık superheat / subcool ölçümü.
- **Fan VFD ayarı:** Servisle birlikte head pressure control yeniden ayarlanmalı; çok düşük frekans HP yükseltir, çok yüksek frekans elektrik israfı.

## Operatöre özet

Yaz aylarında kondenser haftalık görsel kontrol önemli. Sıcaklık-basınç ilişkisi makul ise (her 1 °C dış için ~0.3 bar HP artışı) cihaz sağlıklı. Bu oranın çok üstündeki artış kondenser veya refrigerant problemine işaret eder. Custos bu trendi otomatik takip eder.
