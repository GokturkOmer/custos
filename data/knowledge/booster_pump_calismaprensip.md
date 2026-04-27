---
title: "Booster pump set (Hidrofor) çalışma prensibi"
category: ekipman
asset_template: booster_pump_set
tags: [hidrofor, pompa, basinc, su, vfd]
versiyon: 1.0
yazar: Göktürk
tarih: 2026-04-28
kaynak: Wilo Hydro plus + Grundfos Hydro MPC + Pompa.Org Türkiye saha kılavuzu
---

# Booster pump set (Hidrofor / Basıncı yükseltme grubu) çalışma prensibi

Booster pump set (Türkçe yaygın isim: hidrofor), AVM içinde su ihtiyacı olan noktalara (mağaza WC'leri, food court, teknik servis odaları) yeterli basınçla su sağlamak için kullanılan çoklu pompa grubudur. Şehir şebeke basıncı genelde 2-4 bar; AVM üst katlarında 4-6 bar gerekir. Booster set bu farkı kapatır.

## Temel bileşenler

- **2-6 paralel pompa:** Çoğu zaman 3 pompa standart. Tek pompa kapasite, ikinci pompa yedek, üçüncü pompa senkron yardımcı.
- **Basınç tankı (membran tipi):** Ufak akış için pompa devreye girmesin diye 50-200 L tank.
- **Frekans çevirici (VFD):** Ana pompa modüle çalışır; debi düşükken hız azalır.
- **Sensörler:** Çıkış basınç transmitter, debi sensörü (varsa), seviye (giriş depo).
- **Kontrol paneli:** Sıralama (lead/lag/standby), alarmlar, alarm rölesi.
- **Çekvalfler ve manuel vana:** Geri kaçak önleme, izolasyon.

## Çalışma mantığı

1. Tüketim olduğunda hat basıncı düşer, basınç tankı boşalmaya başlar.
2. Basınç sensörü düşüş okur, kontrolör ana pompayı VFD üstünden çalıştırır.
3. Pompa setpoint basıncını yakalayıp tutar (PID kontrol).
4. Talep arttıkça VFD frekansı yükselir; tek pompa yetmezse ikinci pompa yardıma alınır (cascade).
5. Talep azalınca pompalar sırayla devre dışı kalır.
6. Lead pompa zamanla rotation alır (eşit yıpranma için).

## Kritik parametreler ve normal aralıklar

- **Setpoint basıncı:** AVM tipik 5-7 bar (zone bazlı değişebilir).
- **Setpoint sapması:** ±0.3 bar tolerans; daha fazlası osilasyon.
- **VFD frekansı:** 30-50 Hz çalışma aralığı; sürekli 50 Hz → kapasite yetersiz.
- **Pompa akımı:** Etiket FLA'nın %50-95'i.
- **Seviye (giriş depo):** Üretici belirlediği min/max; düşük seviye kuru çalışma riski.
- **Çalışma sayısı:** Saatte 6-15 start tipik (basınç tankıyla); >25 short-cycle.
- **Dış koruma:** Termik koruma, kuru çalışma koruması, faz koruma.

## Sık karşılaşılan alarmlar / problemler

- **Basınç dalgalanma (osilasyon):** Setpoint çevresinde sürekli yukarı-aşağı.
- **Sürekli max frekans:** Talep > kapasite veya bir pompa devre dışı.
- **Lead pompa trip:** Termik veya kuru çalışma.
- **Düşük basınç alarmı:** Setpoint'in ciddi altı sürekli — boru patlaması, vana kapalı, çekvalf bozuk.
- **Yüksek basınç alarmı:** PID setpoint hatalı veya basınç tankı problemi.
- **Şebeke su kesiliyor:** Giriş seviye düşüyor, koruma devreye giriyor.

## Operatöre kısa rehber

Booster set AVM açıkken sürekli aktiftir. Haftalık kontrol: pompa rotation çalışıyor mu, basınç tankı havası 0.5 bar setpoint'in altında mı, çekvalfler dış sızıntısız mı. Aylık: pompa salmastra (mechanical seal) göz inceleme, motor sıcaklık kontrolü.

Custos `pump_pressure_out` ve `pump_running_count` tag'larını izler. Cascade çalışma desenleri farklılaşırsa otomatik uyarı oluşur (örn. lead pompa hep aynı, lag pompa hiç çalışmıyor → rotation arızası).
