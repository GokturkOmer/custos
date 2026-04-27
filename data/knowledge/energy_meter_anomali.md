---
title: "Enerji ölçer anormal okuma değerlendirmesi"
category: ariza
asset_template: energy_analyzer
tags: [energy_analyzer, anomali, akim, gerilim, harmonik]
versiyon: 1.0
yazar: Göktürk
tarih: 2026-04-28
kaynak: IEEE 519 (harmonik) + Schneider Electric Power Quality kılavuzu
---

# Enerji ölçer anomali değerlendirmesi

Custos enerji analizörlerinden gelen verilerde alarm üretebilen birkaç anomali tipi vardır. Bu doküman her birinin nedenini ve operatör müdahale yaklaşımını özetler.

## Anomali 1: Aktif güç sıfır gibi okurken cihaz çalışıyor

**Belirti:** Custos `active_power_kw` ≈ 0 ama ekipman çalışıyor (örn. chiller akıyor, AHU fan dönüyor).

**Olası sebepler:**
- CT bağlantısı kopmuş (en yaygın). Bir veya birden fazla fazda CT primary açık devre.
- CT polariteleri ters bağlı; aktif güç negatif gelip ortalama sıfır görünür.
- Cihaz CT okumayan tek faz besli (bağlantı planı eski).

**Aksiyon:**
- Anlık gerilim okuması var mı? Varsa CT problemi (CT yok = gerilim okur, akım sıfır gösterir).
- Pens ampermetre ile gerçek akım kontrol; analizör değeri ile fark %5+ ise CT bağlantı sorunu.
- Akredite servis çağrısı; panel açık çalışma izinli kişi.

## Anomali 2: Aktif güç negatif

**Belirti:** Custos `active_power_kw` = -15 kW (ama ekipman üretici değil tüketici).

**Olası sebepler:**
- CT polaritesi ters takılmış (P1-P2 yön bağlantı yanlış).
- Bir faz için CT ters bağlı (3 fazlı sistemde toplam negatife düşer).
- Yazılımda fazlar yanlış eşleşmiş (L1-L2-L3 sırası karışık).

**Aksiyon:**
- Üretici menüsünden faz sırası kontrol (panel reset gerekebilir).
- Servis ekibi CT bağlantı yönünü düzeltir.

## Anomali 3: Voltage flicker / dalgalanma

**Belirti:** Gerilim 220 V civarında olması gerekirken anlık 200-240 V arası zıplıyor.

**Olası sebepler:**
- AVM ana panelinde dengesizlik (büyük yük başlatıyor).
- Şehir şebeke kalitesi (ülke geneli sorun).
- Trafonun yıpranması (saha trafosu).
- Topraklama problemi.

**Aksiyon:**
- Tüm enerji analizörleri aynı ölçüm yapıyorsa şebeke veya trafo problemi → elektrik dağıtım kuruluşu rapor.
- Sadece bir analizör görüyorsa o cihaz / bağlantı problemi.

## Anomali 4: Cosφ (Power Factor) düşük

**Belirti:** Custos `power_factor` = 0.65 (hedef 0.95+).

**Olası sebepler:**
- AVM kompanze tablosu (capacitor bank) arızalı veya kademeleri yetersiz.
- Reaktif güç tüketimi yüksek (büyük motor yükü, eski AHU motorları).
- Harmonik bozulma kompanzasyonu yanıltıyor.

**Aksiyon:**
- Cosφ < 0.95 elektrik dağıtım reaktif ceza çekebilir (Türkiye'de aylık %20 üst sınır).
- Kompanze tablosu servisi acil.
- Custos ortalama Cosφ'yi mart-nisan aylık raporda otomatik dökümle gösterir.

## Anomali 5: Harmonik bozulma yüksek (THD)

**Belirti:** Custos `voltage_thd_pct` veya `current_thd_pct` > %8 (gerilim) veya >%15 (akım).

**Olası sebepler:**
- VFD'li cihazlar (chiller VFD, pompa VFD).
- LED aydınlatma (eski tip ucuz LED'ler).
- UPS sistemleri.

**Aksiyon:**
- THD belirli bir noktanın üstünde sürekli ise harmonik filtresi gerekebilir.
- Harmonik bozulma trafonun ısınmasına ve motor verim kaybına neden olur.
- Pilot için: Custos THD'yi ölçer ve yıllık rapor üretir; aksiyon AVM elektrik mühendisi sorumluluğunda.

## Anomali 6: Beklenmeyen yüksek tüketim (anomali skoru)

**Belirti:** Bir ekipmanın gece 03:00'te çalışmaması beklenen durumda 30 kW tüketim okunuyor.

**Olası sebepler:**
- Gerçek aksiyon: ekipman yanlışlıkla açık kalmış (tasarruf kaybı).
- Sayaç hatalı faz okuyor (komşu cihazın yükü yansıyor).
- Custos baseline yanlış (yeni cihaz, baseline yetersiz veri).

**Aksiyon:**
- Önce ekipman fiziksel kontrolü; gerçekten açık mı?
- Açık ise saatlik program / setpoint planı düzeltme.
- Sayaç hatası şüphesi varsa kalibrasyon kontrolü.

## Custos role özet

Enerji ölçer anomali tespiti F11-D (anomaly engine) ile Critical Loop'ta yapılır. Sıfır güç + cihaz çalışıyor durumu deterministic alarm olarak sınıflanır; trend tabanlı (Cosφ düşüş) raporlama Analytics Loop'a aittir.
