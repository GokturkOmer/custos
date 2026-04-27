---
title: "Sirkülasyon pompası çalışma prensibi"
category: ekipman
asset_template: circulation_pump
tags: [sirkulasyon, pompa, sicak_su, soguk_su, hvac]
versiyon: 1.0
yazar: Göktürk
tarih: 2026-04-28
kaynak: ASHRAE Handbook (HVAC Systems & Equipment, Bölüm 44) + Wilo Stratos + Grundfos Magna3 servis kılavuzları
---

# Sirkülasyon pompası çalışma prensibi

Sirkülasyon pompası, AVM HVAC sisteminde kapalı devre soğuk su (chilled water) veya sıcak su (hot water) hattındaki suyu santraller / FCU'lar / radyatörler arasında dolaştırır. AVM'de tipik kullanım: chiller-AHU arası soğuk su, kazan-radyatör arası sıcak su. Booster pompasından farklı olarak sürekli akış sağlar, basıncı yenilemez.

## Temel bileşenler

- **Pompa gövdesi:** "İn-line" tip (boru üstü, daha yaygın AVM'de) veya "end-suction" tip (büyük debili).
- **Motor:** AC veya VFD'li EC. AVM'de Wilo Stratos / Grundfos Magna gibi entegre VFD'li EC pompalar yaygın.
- **Salmastra (mechanical seal):** Mil ile gövde arasındaki sızdırmazlık.
- **Bearing:** Yağlama yağlı veya su yağlı.
- **Çekvalf (bazı sistemlerde):** Geri akışı önler.
- **İzolasyon vanaları:** Servis için pompa öncesi/sonrası vana.
- **Pompa kontrolörü:** Setpoint (ΔP veya akış), VFD frekansı, alarm rölesi.

## Çalışma mantığı

1. Sistem talebine göre pompa devamlı çalışır (HVAC sistemleri kapalı devre — AVM'de 24/7 yaz, gece azaltılmış kış).
2. VFD'li pompada ΔP setpoint sabit tutulur — talep arttığında frekans yükselir, azaldığında düşer.
3. Pompa kavitasyonu ve termal soruna karşı minimum frekans (genelde 25-30 Hz).
4. Yedek pompa varsa (çift pompa, lead/lag) her start'ta rotation alır.
5. AVM'de sezonluk geçiş: yaz aylarında soğuk su pompası aktif, kış aylarında sıcak su pompası aktif (4 borulu sistemde her ikisi).

## Kritik parametreler ve normal aralıklar

- **ΔP setpoint:** Sistemin hidrolik tasarımına göre 1.0-3.5 bar tipik.
- **VFD frekansı:** %50-90 aralık; sürekli %100 → yetersiz kapasite veya hatalı setpoint.
- **Akım:** Etiket FLA'nın %50-90'ı.
- **Sıcaklık (motor):** Üretici sınırının %20-30 altı; sürekli yüksek motor termik.
- **Titreşim:** ISO 10816 sınıf I sınırının altı (üretici belirler, tipik <2.8 mm/s rms).
- **Salmastra sızıntı:** Damla bazında günlük 1-2; sürekli akış sızıntısı, sızdırmazlık değişimi gerekir.

## Sık karşılaşılan alarmlar / problemler

- **Pompa trip:** Termik koruma, kuru çalışma, kavitasyon.
- **Titreşim alarmı:** Bearing arızası, balanssızlık, bağlantı vidaları gevşek.
- **VFD alarmı:** Aşırı akım, aşırı sıcaklık, faz kaybı.
- **Salmastra sızıntı:** Mekanik conta yıpranmış.
- **Setpoint sapma:** ΔP yakalanamıyor — sistem kompoze değişti veya pompa kapasite kaybetti.
- **Sıcaklık yüksek (motor gövde):** Yetersiz havalandırma, bearing yağı tükenmiş, frekans çok yüksek.

## Operatöre kısa rehber

Sirkülasyon pompaları AVM'de 24/7 çalışır — bakım pencere kısıtlıdır (gece veya bayram tatili). Aylık kontrol: ses + titreşim, salmastra durum, motor sıcaklık. Yıllık servis: bearing yağ kontrolü veya değişim, salmastra durum, balans kontrolü.

Custos `pump_runtime_hours` ve `pump_vibration_rms` tag'larını izler. 5000 saatte bearing yağ değişim hatırlatır; 8000 saatte salmastra inceleme.
