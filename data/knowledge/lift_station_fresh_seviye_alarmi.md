---
title: "Temiz su lift station yüksek seviye alarmı"
category: ariza
asset_template: lift_station_fresh
tags: [lift_station_fresh, seviye, alarm, yagmur_suyu, dalgic_pompa, mahzen]
versiyon: 1.0
yazar: Göktürk
tarih: 2026-04-28
kaynak: Wilo Drain + Grundfos Unilift + saha tecrübesi
---

# Temiz su lift station yüksek seviye alarmı

Temiz (atık olmayan) su terfi istasyonu haznesinde sıvı seviyesi normal pompa-stop noktasını aşıp "yüksek seviye" eşiğine ulaşırsa alarm tetiklenir. AVM'de tipik kullanım yerleri: yağmur suyu drenajı, mahzen / garaj seviye kontrolü, proses tankı dolum öncesi rezerve, sızıntı toplama haznesi.

## Niye önemli

Temiz su tipinde **insan güvenliği** atık su tipindeki kadar kritik değildir (gaz birikimi yok), ama **AVM altyapı zararı** risklidir: mahzen veya garaj döşemesi su altında kalırsa elektrik panoları, mağaza deposu, kablolar zarar görür. Yağmur fırtınası saatlerinde dakika hassasiyetle çalışmalı.

## Belirti

- Custos kritik alarm: "Yüksek seviye — temiz su lift station X".
- Hazne yakınında su sesi, yer kotunda nem.
- Pompa(lar) çalışıyor ama seviye düşmüyor; ya da pompa hiç çalışmıyor.
- Mahzen / garajda zemin yer yer ıslak (taşma yaklaşıyor).

## Olası sebepler

- **Pompa termik trip:** Motor termik koruması açtı. AVM'de yağmur fırtınası saatlerinde ardışık çok-start nedeniyle yaygın.
- **Pompa çark aşınmış:** Yıllarca hizmet sonrası çark verim kaybı; akım normal ama debi düşük.
- **Çekvalf sıkışmış kapalı:** Pompa basıyor ama akış yok. Çıkış basınç var, hazne seviyesi düşmüyor.
- **Çıkış borusu tıkalı veya donmuş:** Kış aylarında dış hatta donma; yıl içinde mineral birikimi.
- **Strainer / Y-süzgeç tıkanması:** Yaprak, polen, inşaat tozu (özellikle yağmur suyu hatları).
- **Anormal yüksek giriş:** Yağmur fırtınası, boru patlağı, soğuk su deposu taşması, sprinkler test sızıntısı.
- **Seviye sensörü hatalı:** Yanlış değer; gerçekte taşma yok ama alarm üretiyor (tersi de olabilir — gerçek taşma var ama düşük gösteriyor).
- **Elektrik kesintisi:** Pompa istasyonu beslemesi kesik; UPS yok ise pompa duruyor.
- **Çift pompa arızası:** Hem duty hem standby çalışmıyor.

## Aciliyet

**Orta-Yüksek.** AVM mahzeni / garajı varsa 1-2 saat içinde döşeme zarar görmeye başlar; elektrik panosu yakınında ise saatlik tepki gerek. Hazneye girme açısından profesyonel ekip beklenmez (gaz riski yok), ama elektriksel emniyet + ıslak zemin kayma riski göz önünde.

## Kontrol adımları

1. Custos alarm sayfasından pompa durumunu oku — duty / standby ne durumda? Akım, çalışma süresi, son start zamanı.
2. Pompa akımı 0 → motor trip veya elektrik kesik. Termik reset; tekrar trip ediyorsa motor sargı testi.
3. Pompa akımı yüksek + akış yok → çark aşınması veya çekvalf sıkışması. Pompayı durdur, bypass varsa devreye al.
4. Pompalar çalışıyor + akım normal + seviye düşmüyor → çekvalf veya çıkış borusu tıkalı/donmuş.
5. Hazne seviye sensörünü görsel doğrula: gerçekten yüksek mi? Yüzer flat hareket ediyor mu? Hidrostatik basınç sensörü kabel temiz mi?
6. AVM dış havası kontrol: yağmur fırtınası sürüyor mu? Saatlik giriş kapasiteyi aşmış mı?

## Kısa vadeli aksiyon (operatör)

1. **Custos arayüzünden bakım modu** aç — alarm gürültüsünü sustur, AVM yönetimini bilgilendir.
2. AVM teknik servis çağrı (push notification + telefon).
3. Hazneye giriş **gaz açısından** güvenlidir ama elektriksel emniyet + slip / kayma + boğulma riski (yüksek seviyede) açısından ekipman + ikinci kişi gözetim önerilir.
4. Yağmur fırtınası nedeniyle ise: dış kanalizasyon / drenaj boşaltma kapasitesini de kontrol — bazen problem lift station değil dış altyapıdadır.

## Kalıcı çözüm

- **Periyodik pompa servisi:** Yılda 1 kez bearing yağı, salmastra, kabel izolasyon testi.
- **Çark periyodik kontrol:** 3-5 yılda bir aşınma testi; verim düşüşünde değişim.
- **Strainer / ızgara temizliği:** Yağmur suyu istasyonlarında her sezon başı (sonbahar yapraklarından önce + ilkbahar polen sonrası).
- **Çekvalf bakım:** Yılda 1 kez sökülüp temizlik.
- **UPS değerlendirme:** Kritik AVM altyapısı için elektrik kesintisinde 30-60 dakikalık pompa beslemesi sağlayan UPS.
- **Yağmur olayları:** Meteoroloji uyarısı + AVM yönetim koordinasyon (geçici dış pompa hazır bulundurma yağışlı sezon).

## Custos rolü

Custos `lift_high_level` tag'ı 1 olduğunda kritik alarm + push notification gönderir (READY README §14 PAT). Pompa runtime + start sayısı + akım izlemesi predictive maintenance girişi. Custos asla pompa start/stop komutu vermez (yazma yasak); kontrol local PLC veya BMS üstündedir.

## Operatöre özet

Temiz su yüksek seviye alarmı genelde "AVM altyapı korumasıdır", insan güvenlik açısından kritik değil ama mali zarar açısından gerçek. Profesyonel olmayan personel hazneye girebilir ama elektrik + slip emniyeti şart. Yağmur olaylarında dakika hassasiyetle takip edilmeli.
