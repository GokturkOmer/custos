---
title: "Lift station (Atık + temiz su terfi) çalışma prensibi"
category: ekipman
tags: [lift_station, lift_station_fresh, lift_station_waste, terfi, atik_su, temiz_su, dalgic_pompa, seviye_sensor]
versiyon: 1.0
yazar: Göktürk
tarih: 2026-04-28
kaynak: ASHRAE Plumbing references + Wilo Drain & Sewage + ABS / Sulzer servis kılavuzları
---

# Lift station çalışma prensibi

Lift station (terfi istasyonu), bir AVM içinde yer çekimine karşı su veya atık suyu daha üst kotaya pompalayan otomatik istasyondur. İki ana tip:

- **Temiz su terfi (lift_station_fresh):** Şehir suyu / yağmur suyu / proses suyu gibi temiz akışkanlar için.
- **Atık su terfi (lift_station_waste):** Sifon, lavabo, food court grease trap çıkışı, foseptik içerikler için.

AVM bodrum / bodrum eksi katlarında bu istasyonlar zorunludur — atık dışarıya yer çekimiyle ulaşamaz.

## Temel bileşenler

- **Toplama haznesi (sump pit):** Beton veya plastik, 500-5000 L hacim. Atık su tipinde yağ ayırıcı veya katı tutucu eklenir.
- **2 dalgıç pompa (duty + standby):** Atık su tipinde "non-clog" veya "vortex" çark; temiz su tipinde standart santrifüj.
- **Seviye sensörleri:** Yüzer (float switch) veya hidrostatik basınç (4-20 mA). Tipik 3-4 seviye: pompa-1 start, pompa-2 start, alarm-yüksek, kuru-koruma.
- **Kontrol paneli:** Pompa rotation, alarm rölesi, pompa start sayacı, çalışma saati.
- **Çıkış borusu + çekvalf:** Geri akışı önler; her pompada bağımsız.
- **Havalandırma:** Atık su tipinde gaz birikiminin tahliyesi için zorunlu.
- **Yüksek seviye alarmı:** Bağımsız flat veya seviye sensörü; ana sensör arızası durumunda yedek.

## Çalışma mantığı

1. Hazne dolar; seviye-1 setpoint'ine ulaşınca duty pompa start eder.
2. Pompa hazneyi boşaltır; seviye-stop noktasına inince durur.
3. Her start'ta lead/lag rotation: bir sonraki dolma duty'yi diğer pompaya verir (eşit yıpranma).
4. Tek pompa yetmezse seviye-2 (yardım) noktasında ikinci pompa devreye girer.
5. Hazne taşma yaklaşırsa "yüksek seviye alarmı" — Custos kritik alarm üretir.
6. Atık su tipinde gaz tetiklerse + pompa boş çekerse "kuru koruma" devreye girer.

## Kritik parametreler ve normal aralıklar

- **Pompa start/stop sayısı:** Saatte 4-12 normal; >20 short-cycle veya tıkanma.
- **Pompa akımı:** Etiket FLA'nın %60-95'i; çark tıkalıysa akım yüksek + akış yok.
- **Çalışma süresi:** Tipik start başına 1-3 dakika; >5 dakika sürekli → kapasite yetersiz.
- **Seviye sensörü:** Setpoint'lere göre çalışıyor; aynı seviyede 2 sensör tutarsızsa biri arızalı.
- **Duty/standby rotation:** Her pompa eşit oranda çalışmalı. Custos `pump_runtime_hours` her ay rapor.

## Sık karşılaşılan alarmlar

- **Yüksek seviye alarmı:** Pompa kapasitesi düşük, tıkanma, elektrik kesintisi, seviye sensörü arızası.
- **Pompa trip:** Termik veya kuru çalışma, sıkışmış katı.
- **Düşük seviye / kuru koruma:** Pompa sürekli boşa çalıştı, sensör arızası veya gerçek su yokluğu.
- **Rotation çalışmıyor:** Tek pompa hep duty, diğer hep standby. Eşitsiz yıpranma.
- **Yüksek akım:** Çark tıkalı, salmastra sıkışmış, bearing arızalı.
- **BMS / Modbus kayıp:** Custos'a veri gelmiyor — kabel veya kart problemi.

## Güvenlik notu (atık su tipinde)

Atık su lift station haznesinde **H₂S (hidrojen sülfür)** ve **metan** gazı birikebilir. Bu hazneye giriş confined space prosedürü gerektirir — gaz ölçer + havalandırma + ikinci kişi gözetim. Çalışan personel hayatını kaybedebilir.

Custos havalandırma fan tag'ı varsa izlenir; yoksa pilot kurulumda eklenmesi şiddetle önerilir.

## Operatöre kısa rehber

Lift station haftalık ses + titreşim kontrolü ister; hazneye girilmez sadece dış görsel + sensör değerleri yeterli. Aylık rotation ve çalışma sayısı raporu Custos'tan otomatik. Yıllık servis: pompa salmastra, çark balansı, kabel izolasyon testleri.
