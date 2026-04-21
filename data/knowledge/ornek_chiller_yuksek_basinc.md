---
title: "Chiller yüksek deşarj basıncı"
category: ariza
asset_template: chiller
tags: [chiller, basinc, alarm, kondenser]
---

# Chiller yüksek deşarj basıncı

Kompresör deşarj tarafındaki basınç üreticinin belirlediği eşiğin (tipik 10-15 bar) üstüne çıktığında bu alarm tetiklenir. Devam ederse HP switch'i kompresörü durdurur.

## Olası sebepler

- Kondenser fanı çalışmıyor veya yavaş dönüyor
- Kondenser yüzeyi tozlu / tıkalı
- Ortam sıcaklığı mevsim normallerinin üstünde
- Refrigerant sisteme fazla şarj edilmiş
- Su kondenseri ise: su debisi düşük veya sıcaklık yüksek

## Kontrol adımları

1. Kondenser fanının (veya fanlarının) çalıştığını gözle doğrula
2. Fan motor akımını panel üstünden oku; tipik değerin dışında mı?
3. Kondenser yüzeyini görsel incele — kir, yaprak, hasar
4. Ortam sıcaklığını not al; refrigerant basıncı ile karşılaştır
5. Refrigerant sight-glass'ta köpüklenme / flaş var mı?

## Kısa vadeli aksiyon

- Kondenser temizliği ile çözülmüyorsa kompresörü kapat, yetkili servise bilgi ver.
- Kapatma kararını panel üzerinden değil SCADA'dan al (Custos yazma yapmaz).
