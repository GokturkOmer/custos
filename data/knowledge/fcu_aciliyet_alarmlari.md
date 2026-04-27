---
title: "FCU acil alarmları ve müdahale"
category: ariza
asset_template: fcu
tags: [fcu, alarm, fan, kondens, vana]
versiyon: 1.0
yazar: Göktürk
tarih: 2026-04-28
kaynak: ASHRAE Handbook + saha tecrübe (Custos pilot ön çalışma notları)
---

# FCU acil alarmları ve müdahale

Bu doküman FCU üzerinde Custos sisteminin yakaladığı acil alarmları ve operatörün yapması gerekenleri özetler. FCU küçük bir cihaz olduğu için tek başına acil müdahale gerekmez; ancak mahalin (örn. bir ofis veya teknik oda) konforunu / güvenliğini etkilediği için hızlı dönüş ister.

## Alarm 1: Kondens tabağı taşma sensörü

**Belirti:** FCU altında veya yan tarafında bulunan flat sensör, su seviyesi belli bir noktayı geçtiğinde sinyal verir. Custos `fcu_condensate_alarm` tag'ı 1 olur.

**Aciliyet:** Yüksek — su tavandan sızıyorsa yaklaşık 30 dakika içinde mağaza ürününe veya elektriksel donanıma zarar verir.

**Aksiyon:**
1. FCU bulunduğu mahalin tam altına ulaş.
2. FCU'yu acil durdur (BMS / Custos asset detayında "ekipman duraklat" butonu — Custos yazma yapmadığı için aslında SCADA üstünden işlem).
3. Drenaj borusunu kontrol et; tıkanma varsa açıcı tel veya basınçlı su.
4. Drenaj eğimi kontrolü (1-2% düşey).
5. Kondens tabağı eğimsiz veya kırıksa servis çağrısı.

## Alarm 2: Fan trip (termik)

**Belirti:** Fan motoru termik koruma açtı, fan dönmüyor. Custos `fcu_fan_status` = stopped, akım sıfır.

**Aciliyet:** Orta — oda sıcaklığı 30-60 dakikada konfor dışına çıkar.

**Aksiyon:**
1. Termik koruma butonu sıfırla (panel üstünden).
2. Fan tekrar trip ediyorsa motor sargı testi gerek (multimetre ile direnç).
3. Kayış varsa görsel kontrol (eski tip).
4. Kapasitör (kondansatör) tip motorda kapasitör değişimi gerekebilir.
5. Aksiyon yetkili teknik personelce yapılır; arızalı FCU varsa AVM yönetimi mahal kullanım kısıtlama kararı alır.

## Alarm 3: Vana arızalı veya kapalı kalmış

**Belirti:** Soğutma talimatı %100 ama vana açıklığı %0 (ya da tersi). Setpoint sapması persistent.

**Aciliyet:** Düşük-Orta — konfor etkisi var, donanım hasarı yok.

**Aksiyon:**
1. Vana aktüatörü manuel pozisyonlama (varsa).
2. Aktüatör motor değişim (Belimo, Honeywell, Siemens markaları yaygın, parça stokta tutulur).
3. Servis sırasında kontrol kablosu da kontrol edilmeli — bazen sinyal yok arızası gibi görünür ama kabel kopuğudur.

## Alarm 4: Filtre tıkalı (ΔP yüksek veya hava akışı düşük)

**Belirti:** FCU küçük olduğu için ayrı ΔP transmitter'ı genelde yok. Custos bunu dolaylı olarak setpoint sapması + fan akımı düşüklüğü ile yakalar.

**Aciliyet:** Düşük — sağlık etkisi var ama ekipman zararı yok.

**Aksiyon:**
1. Filtre kapağı aç + görsel.
2. Genelde 3-6 ay yıkanabilir filtre, 6-12 ay değiştirilebilir filtre.
3. AVM saha tozluluğu yüksekse periyot kısalır.

## Alarm 5: BMS / Modbus bağlantı kopuk

**Belirti:** Custos FCU instance'ından veri gelmiyor, last_seen 5+ dakika.

**Aciliyet:** Orta — ekipman aslında çalışıyor olabilir, sadece izleme kayıp.

**Aksiyon:**
1. Modbus RS-485 hattı kontrolü (sonlandırma direnci, mesafe, kabel).
2. Custos collector loglarında bu device için hata kodu okuma.
3. FCU panelinde manual reset.
4. Tekrar veri gelmiyorsa elektronik kart problemi; servis çağırılır.

## Operatöre özet

Tek FCU alarmı acil tepki ister sadece (1) kondens taşması ve (2) fan trip durumlarında. Diğer alarmlar planlı bakım kapsamında ele alınır. AVM'de 100+ FCU varsa rapor tabanlı toplu müdahale (haftalık) en verimlisi; Custos bu raporu otomatik üretir.
