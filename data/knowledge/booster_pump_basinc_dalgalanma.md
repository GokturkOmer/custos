---
title: "Booster pump basınç dalgalanması (osilasyon)"
category: ariza
asset_template: booster_pump_set
tags: [hidrofor, pompa, basinc, dalgalanma, pid, vfd, cekvalf]
versiyon: 1.0
yazar: Göktürk
tarih: 2026-04-28
kaynak: Wilo + Grundfos servis kılavuzları + saha tecrübesi
---

# Booster pump basınç dalgalanması

Çıkış basıncı setpoint'in çevresinde sürekli yukarı-aşağı zıplıyorsa (osilasyon) sistem stabil değildir. AVM kullanıcısı musluk açtığında "su göğüs vuruyor" tarif eder. Hidrolik darbeler boruları, vanaları ve ek-çıkışları yıpratır.

## Belirti

- Custos `pump_pressure_out` grafiğinde dakikada 4-10 osilasyon gözlüyor.
- VFD frekansı sürekli yukarı-aşağı; pompa hız değişimi belirgin.
- Pompalar saatte 30+ start/stop (tipik 6-15 olmalı).
- AVM kullanıcı şikayetleri: basınç hissi kararsız.

## Olası sebepler

- **Basınç tankı havası boşalmış:** En yaygın sebep. Membran tipi tankta hava 0.5 bar setpoint'in altında olmalı (örn. 5 bar setpoint için tank ön basıncı 4.5 bar). Hava boşalırsa tank tampon görevini yapamaz, pompa sürekli takip eder.
- **Çekvalf sızıntılı:** Pompa dururken hat basıncı geri kaçıyor. Pompa hemen tekrar devreye giriyor.
- **PID parametreleri yanlış ayarlanmış:** Proportional çok yüksek (osilasyon) veya çok düşük (yavaş tepki).
- **Setpoint çok düşük:** Talep ile kapasite arasında dar marj — küçük tüketim bile sistemi tetikliyor.
- **VFD'de minimum frekans çok yüksek:** Pompa çok yüksek hızda devreye giriyor, sönümleme kalkmamış.
- **Birden fazla pompa cascade kuralı yanlış:** Lag pompa devreye girip çıkarken lead'in iş yükünü zıplatıyor.
- **Hat içinde hava:** Sistemi havalandırmadan devreye alma sonrası — basınç darbeleri.
- **Sensör hatalı:** Transmitter bozuk veya kalibre kaymış, gerçek basınç değil yanlış değer okuyor.

## Kontrol adımları

1. Pompa kapalı, sistem basınçsız (tahliye vanasından boşalt) iken basınç tankı havasını manometre ile ölç. Setpoint −0.5 bar olmalı; değilse hava ekle (otomobil pompa veya kompresör).
2. Çekvalfleri tek tek manuel kontrol: pompa kapalı + sistem basınçlı iken çıkışta sızıntı var mı?
3. PID parametrelerini servis kayıtlarından kontrol; üretici default değerlerinden farklıysa nedeni biliniyor mu?
4. Setpoint seviyesi AVM'in en üst noktasındaki kullanım için yeterli mi (her kat ~0.1 bar)?
5. VFD min frekansı 25-30 Hz aralığında olmalı.
6. Sensörü manuel basınç ölçer (manometre) ile kıyasla; >0.2 bar fark varsa kalibre.

## Kısa vadeli aksiyon

- Basınç tankı havası eksikse hemen ekle — en hızlı çözüm, çoğu vakada yeterli.
- Setpoint'i 0.3-0.5 bar yükseltmek osilasyonu sönümler ama elektrik tüketimi biraz artar.
- VFD ramp-up süresi parametresini uzatmak başlatma darbelerini yumuşatır.

## Kalıcı çözüm

- **Periyodik tank havası kontrolü:** Yılda 2 kez (mart + eylül).
- **Çekvalf değişim:** Tüm pompalarda 5-7 yıl, AVM gibi sürekli kullanımda 4-5 yıl.
- **PID re-tune:** Otomasyon mühendisi servis ziyaretinde; auto-tune fonksiyonu kullanılırsa not düşülmeli.
- **Sensör kalibrasyon kayıtları:** Yılda 1 kez Custos kayıtlarına geç.

## Operatöre özet

Basınç dalgalanması ihmal edilirse sıvı darbesi ile boru ek yerinde sızıntı 6-12 ayda kaçınılmazdır. Aksiyon süresi sınırlı: ya tank havası yenile ya da çekvalf servis. Custos osilasyon paterninin şiddeti / frekansı bazında öncelik sırası önerir; pilot kurulumda bu öneri henüz manuel servis çağrısı olarak süzülür.
