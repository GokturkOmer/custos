---
title: "Alarm geldiğinde genel yaklaşım rehberi"
category: sistem
tags: [alarm, prosedur, operator, custos, mudahale]
versiyon: 1.0
yazar: Göktürk
tarih: 2026-04-28
kaynak: ISA-18.2 alarm management + Custos brief v1.7 §4.7
---

# Alarm geldiğinde genel yaklaşım rehberi

Custos sistemi alarm ürettiğinde operatör belirli bir prosedürü takip etmelidir. Panik veya tek başına müdahale en yaygın hatadır; sakin değerlendirme + servis tetikleme + kayıt en iyi pratiktir.

## Alarm sınıfları (Custos)

Custos brief v1.7 §4.7'ye göre alarmlar 3 seviyede sınıflanır:

- **info** — Bilgilendirici. Operatör görür, aksiyon zorunlu değil.
- **warn** — Uyarı. 4-24 saat içinde bakma / aksiyon gerekir.
- **crit** — Kritik. 0-2 saat içinde aksiyon. Push notification + sesli/görsel uyarı.

## 7 adımlı genel yaklaşım

### 1. Sakin oku

Alarm gelir gelmez panel başına geçip her butona basmayın. Alarm metnini okuyun. Hangi ekipman? Hangi tag? Hangi sınıf? Custos arayüzü tüm bilgileri gösterir.

### 2. Sınıfı belirle

- **info / warn:** Operasyon devam, planlama yap.
- **crit:** Acil aksiyon, ama hala aşağıdaki adımları sırayla.

### 3. Bağlam topla

Alarm tag'ının grafiğini son 1 saat / 24 saat / 7 gün açın. Trend nedir?

- Ani sıçrama → donanım arıza ihtimali.
- Yavaş yükseliş → yıpranma / kirlenme.
- Tekrarlayan desen → kontrol problemi.

### 4. Custos önerilerini kontrol et

Custos asistan modülü (F8b) bu doküman setini indeksler. Asistan sayfasında alarm metnini soru olarak yapıştır; ilgili müdahale dokümanı (örn. `chiller_low_pressure_alarm.md`) önerilir.

### 5. Kontrol adımlarını uygula

Doküman içindeki adımları sırayla:

1. Görsel kontrol (kanama, koku, ses, kıvılcım, ısı).
2. Yan tag'lar (aynı ekipmanın diğer ölçümleri uyumlu mu?).
3. Üretici paneli (alarm logu, son trip kodu).
4. Saha çalışan paneli (varsa).

### 6. Karar ver

- Operatör kapsamı içinde mi (filtre değişim, görsel temizlik, vana açma)? Yap.
- Servis gerek mi? Custos bakım modu aç + servis çağrı.
- Acil değil ama servis takvime alınmalı? Custos planlayıcıya not.

### 7. Kayıt al

Aksiyon ne olursa olsun Custos bakım sayfasına not düş:

- Tarih + saat
- Alarm tipi + tag
- Aksiyon
- Sonuç (çözüldü / takip / servis bekliyor)
- Notlar (gözlem, fotoğraf, harici döküman referansı)

Kayıt yoksa olay olmamış sayılır — servis raporlarına da bağdaştırılamaz.

## Yapılmaması gerekenler

- **Custos üzerinden ekipmana yazma yapma denemesi:** Custos sadece okur, asla yazmaz. Ekipman kontrolü local PLC veya BMS üstündedir.
- **Alarm sussun diye reset basma:** Sebebi bilmeden reset = aynı alarm 5 dakika sonra geri.
- **Custos bakım modu açıp unutma:** Bakım modu aktifken alarmlar baskılanır; servis bittikten sonra mutlaka kapat.
- **Tek başına kapalı alana giriş:** Lift station haznesi, chiller içi vb. confined space gerektirir; bunlara tek başına girilmez.
- **Üretici prosedürü dışı kalibrasyon / şarj:** Refrigerant şarjı, vana ayar, PID değişimi sertifikalı kişinin işidir.

## Aciliyet sınıfına göre tepki süresi

- **crit (kritik):** 0-30 dakika tepki, 1-2 saatte aksiyon.
- **warn (uyarı):** Aynı vardiyada okuma, sonraki vardiya öncesi aksiyon planı.
- **info:** Haftalık raporlama yeterli.

## Pilot kurulumda akış (Torunlar GYO)

1. Alarm gelir → Custos push notification (READY README §14 PAT) + AVM teknik servis whatsapp.
2. AVM teknik servis Custos arayüzü açar, alarm sayfasını inceler.
3. Sınıf info/warn → kendi takip kuyruğuna alır.
4. Sınıf crit → AVM yönetimi bilgilendirilir, dış servis (chiller marka servisi vs.) gerekirse çağrılır.
5. Aksiyon sonrası Custos bakım sayfasına kayıt; ay sonu rapor otomatik üretilir.

## Operatöre özet kontrol listesi

- [ ] Alarm metnini oku, sınıfı belirle.
- [ ] Custos arayüzünden tag grafiği aç.
- [ ] Asistana sor (eğer doküman varsa).
- [ ] Görsel + saha kontrolü.
- [ ] Aksiyon kararı (kendi / servis / takvime al).
- [ ] Kayıt al.

Bu liste her crit alarm için takip edilmelidir; warn için kısaltılmış versiyon yeterli.
