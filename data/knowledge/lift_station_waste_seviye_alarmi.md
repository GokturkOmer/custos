---
title: "Atık su lift station yüksek seviye alarmı"
category: ariza
asset_template: lift_station_waste
tags: [lift_station_waste, seviye, alarm, atik_su, h2s, gaz, dalgic_pompa, tikanma]
versiyon: 1.0
yazar: Göktürk
tarih: 2026-04-28
kaynak: Wilo Drain & Sewage + Sulzer ABS XFP servis kılavuzları + saha tecrübesi
---

# Atık su lift station yüksek seviye alarmı

Atık su terfi istasyonu (food court, sifon, lavabo, mutfak çıkışları) haznesindeki sıvı seviyesi normal pompa-stop noktasını aşıp "yüksek seviye" eşiğine ulaşırsa kritik alarm tetiklenir. **Bu alarm Custos'un en yüksek aciliyet sınıfındadır:** AVM içine atık su taşması, biyolojik kontaminasyon, hayat tehlikesi (gaz) riskleri birden devrededir.

## Niye kritik

İki ayrı tehlike:

- **Operasyonel:** 30 dakikadan az sürede AVM içine taşma; ürün, mağaza, müşteri etkilenir; sağlık otoritesi raporu çekebilir, geçici kapanma maliyeti yüksek.
- **İnsan güvenliği:** Atık su haznesinde **H₂S (hidrojen sülfür)** ve **metan** gazı birikir. H₂S 100 ppm üstünde dakikalarda öldürür, kokusuz hale gelir (koku desensitizasyonu). Profesyonel olmayan giriş ölümle sonuçlanmıştır.

## Belirti

- Custos kritik alarm: "Yüksek seviye — atık su lift station X".
- Hazne yakınında atık su / kanalizasyon kokusu (her zaman değil — H₂S yüksek konsantrasyonda kokusuzdur).
- Pompa(lar) çalışıyor ama seviye düşmüyor.
- Food court veya AVM tuvaletlerinde gerikaçma şikayetleri.
- Bodrumda atık su göllenmesi (taşma başladı).

## Olası sebepler

- **Pompa(lar) tıkalı:** En yaygın sebep. AVM food court atığı (yağ + katı), hijyenik bez, plastik poşet çarka sarılır. Akım yüksek + akış yok klasik belirtisi.
- **Pompa termik trip:** Tıkanma akımı yükseltir, koruma açar.
- **Çekvalf sıkışmış kapalı:** Pompa basıyor ama akış yok.
- **Çıkış borusu tıkalı:** Üst kotaya çıkış hattında yağ + katı birikimi; AVM food court üst tarafı tipik problem yeri.
- **Elektrik kesintisi:** UPS yoksa pompa duruyor.
- **Seviye sensörü hatalı:** Yanlış değer; gerçekte taşma var ama düşük gösteriyor (daha tehlikeli) ya da ters.
- **Anormal yüksek giriş:** Food court yoğun saat (öğle/akşam pic), AVM yoğunluk dönemleri.
- **Çift pompa arızası:** Lead+lag birlikte arızalı (rotation çalışmıyorsa biri zaten ölü demek).

## Aciliyet

**ÇOK YÜKSEK** — Custos sınıfı `crit`. Push notification + AVM teknik servis WhatsApp + AVM yönetim bilgilendirme paralel tetiklenir. Gaz riski + AVM içine taşma birden vardır; tek başına insanın hazneye yaklaşması bile çok yakın olmamalıdır.

## Kontrol adımları (uzaktan, hazneye yaklaşmadan)

1. Custos alarm sayfasından pompa durumunu oku — duty/standby akım, çalışma süresi, son trip kodu.
2. Pompa akımı yüksek + akış yok → tıkanma kuvvetle muhtemel. Yetkili servis çağrısı.
3. Pompa akımı 0 → motor trip veya elektrik kesik. Yetkili servis.
4. Pompalar çalışıyor + akım normal + seviye düşmüyor → çekvalf veya çıkış borusu yağ+katı tıkalı.
5. Custos giriş debi tag'ı varsa kontrol — anormal yüksek giriş mi (food court pic), yoksa pompa kapasitesi mi yetersiz?

## ACİL EMNİYET KURALLARI

- **Hazneye TEK BAŞINA GİRİLMEZ.** Profesyonel olmayan personel için confined space prosedürü zorunlu: gaz ölçer (4-gas, H₂S/CO/O₂/LEL), zorla havalandırma fan, ikinci kişi gözetim, kurtarma teçhizatı (üçayak + körük).
- **Hazne kapağı bile açılmadan** önce gaz ölçümü uzaktan prob ile yapılmalı.
- **Hazne yakınında ateş, kıvılcım, sigara YASAK** (metan = patlama riski).
- AVM operatörü bu işlemleri yapmaz; sadece yetkili dış servis ekibi (kanalizasyon / pompa servis firması) yapar.

## Operatör aksiyonu

1. **Custos arayüzünden bakım modu** aç — alarm gürültüsünü sustur, devam alarmları tekrar push edilmesin.
2. AVM teknik servis acil çağrı (push notification otomatik gider, telefon konfirmasyon).
3. AVM yönetimi bilgilendir — food court / etkilenen alanın geçici kapatılma kararı verebilir.
4. Hazne yakınına insan yaklaştırma; çevre güvenli bant ile çevreleme.
5. Servis ekibiyle telefon iletişim: pompa marka/model/seri no, alarm zamanı, son çalışma kayıtları (Custos'tan).

## Kalıcı çözüm

- **Pompa çark tipi seçimi:** Tıkanma tekrarlıyorsa "non-clog" (semi-vortex) yetersiz; **vortex** veya **grinder/cutter** tipi düşünülmeli. Çark tipi değişimi büyük revizyon.
- **Hazne giriş ızgarası:** Katıların pompaya ulaşmasını engelleyen tarama yapısı (bar screen) — periyodik temizlik gerekir ama pompa ömrünü uzatır.
- **Food court grease trap kontrolü:** Aylık temizlik + servis takvimi. Atık yağın kanalizasyona girmesi en büyük tıkanma sebebidir.
- **Periyodik hazne temizliği:** Yılda 2 kez kapsamlı (akredite atık su firması) — biriken çamur + yağ tabakası alınır.
- **Biyosit / koku kontrolü:** Bazı tesislerde otomatik dozaj sistemi (gaz birikimi azaltır, koku problemi azalır).
- **Yedek pompa stoğu:** AVM teknik depoda 1 adet özdeş yedek pompa (acil değişim için).
- **Havalandırma fan testi:** Yılda 1 kez fan + duct sistem kontrolü; gaz tahliyesi sürekli çalışmalı.

## Custos rolü

Custos `lift_high_level` tag'ı 1 olduğunda **kritik öncelik** push notification gönderir (Pilot READY README §14 PAT). Pompa start/stop sayıları + akım izlemesi predictive maintenance girişidir; tıkanma trendi yakalanırsa erken servis önerisi otomatik üretilir. Custos asla pompa start/stop komutu vermez (yazma yasak); kontrol local PLC veya BMS üstündedir.

## Operatöre özet

Bu alarm geldiğinde panik yok ama **profesyonel olmayan müdahale yasak**. Aksiyon: bakım modu + servis çağrısı + AVM yönetim bilgi + hazne çevre güvenlik. Pompa tıkanması en sık sebeptir, mekanik servis 1-3 saatte çözer. AVM food court grease trap takibi en güçlü önleyici tedbir.
