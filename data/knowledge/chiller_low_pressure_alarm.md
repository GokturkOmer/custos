---
title: "Chiller düşük emiş basıncı (LP) alarmı"
category: ariza
asset_template: chiller
tags: [chiller, basinc, alarm, refrigerant, evaporator]
versiyon: 1.0
yazar: Göktürk
tarih: 2026-04-28
kaynak: ASHRAE Refrigeration Handbook + York YK / Trane CVHE servis notları
---

# Chiller düşük emiş basıncı alarmı

Kompresör emiş tarafındaki basınç (suction / low side / LP) üreticinin alt sınırın altına düşerse cihaz LP trip ile durur. R-134a için tipik LP eşiği 1.8-2.2 bar, R-410A için 5.5-6.5 bar mertebesindedir. AVM saha şartlarında bu alarm en sık görülen 3 chiller alarmından biridir ve genellikle gerçek bir donanım problemine işaret eder; baypas edilmemelidir.

## Niçin önemli

Düşük emiş basıncı = düşük buharlaşma sıcaklığı. Eğer bu sıcaklık donma noktasının altına inerse evaporator boruları içindeki proses suyu donar — boru çatlağı, eşanjör ölümü, hafta-aylık duruş riski. Donma koruması (anti-freeze) bu alarmla iç içe çalışır.

## Olası sebepler

- **Refrigerant kayıp:** En yaygın sebep. Sızdıran flare, schrader vana veya eşanjör çatlağı. UV veya elektronik sniffer ile tespit gerekir.
- **Evaporator kireçlenmesi (scaling):** Boru iç yüzeyinde kalsiyum + magnezyum birikimi. Su tarafından refrigerant'a ısı transferi azalır → buharlaşma azalır → emiş basıncı düşer.
- **Düşük su debisi:** Pompa arızası, tıkalı strainer, kapanmış vana, hava sıkışması. Akış altı %70'in altına inerse LP trip kaçınılmaz.
- **Kirli su tarafı filtreleri:** Strainer / Y-süzgeç tıkanıklığı.
- **Genleşme valfi (TXV) hatalı çalışıyor:** Tıkalı veya bozuk superheat sensörü → valf yetersiz açılıyor.
- **Düşük dönüş suyu sıcaklığı:** Bina yükü çok az + setpoint çok düşük; dönüş suyu setpoint'e yakın geliyor.

## Kontrol adımları

1. Custos `evap_return_temp` ve `evap_supply_temp` grafiğini incele — ΔT 5 °C'nin altında mı?
2. Pompa akımı ve fark basınç ölçümü; debi etiket değerinin %85+ olmalı.
3. Strainer / filtre kontrolü; tıkanmışsa vanalı izolasyondan sonra temizlik.
4. Refrigerant sight glass: sürekli köpük varsa şarj eksik veya filter-drier tıkalı.
5. Üretici panelinden superheat değerini oku; hedef genelde 4-7 K. Çok yüksek superheat → TXV az açıyor; çok düşük → fazla şarj.
6. Son refrigerant şarj tarihinin üstünden 6 ay+ geçtiyse ve LP düşüyorsa sızıntı taraması zorunlu.

## Kısa vadeli aksiyon

Cihaz kendi LP trip mantığı ile zaten durdurulur. Operatörün yapması gereken, alarm sebep kategorisini fotoğraflayıp yetkili servise iletmek ve cihazı **manuel reset etmemek** — koruma anlamlı bir donanım sinyalidir. AVM operatörü olarak Custos arayüzünden bakım modu kaydı açıp servis çağrısını başlatın.

## Kalıcı çözüm

- Sızıntı tespit edilirse onarım + tam vakum + üretici şarj prosedürü.
- Scaling tespit edilirse kapalı devre kimyasal temizlik (CIP) + su şartlandırma kontrolü.
- TXV bozuksa değişim ve superheat ayarı (üretici prosedürü).
- Tüm aksiyonlar Custos bakım takvimine kayıt; bir sonraki periyodik kontrolde tekrar.
