# Bilgi Tabanı (Knowledge Base)

Bu dizin teknik asistan chatbot'un (F8b) kaynak dokümanlarını barındırır. Chatbot dokümanları indeksler ve operatörün sorusuyla semantic search ile eşleştirir — **LLM kullanılmaz, cevaplar yalnızca buradaki dokümanlardan gelir.**

## Desteklenen formatlar

### 1. Markdown dosyaları (`*.md`)

Uzun dokümanlar — sistem çalışma prensibi, ekipman açıklaması, bakım prosedürü. Her `##` başlığı ayrı bir chunk olur.

**Frontmatter şablonu:**

```markdown
---
title: "Chiller yüksek deşarj basıncı"
category: ariza            # sistem | ekipman | ariza | bakim
asset_template: chiller    # opsiyonel — ilgili asset template slug'ı
tags: [chiller, basinc, alarm]
---

# Chiller yüksek deşarj basıncı

Girizgah metni — bu, başlıksız chunk olarak indekslenir.

## Olası sebepler

- Kondenser fanı çalışmıyor
- Kondenser yüzeyi kirli
- Refrigerant fazla
- Ortam sıcaklığı yüksek

## Kontrol adımları

1. Kondenser fanının çalıştığını doğrula
2. Kondenser yüzeyini görsel incele
3. Refrigerant basınç değerlerini oku
```

Her `##` başlığı başlığı + alt içeriği ile beraber tek chunk'tır. Başlığın üstündeki girizgah ayrı bir chunk olur (sadece varsa).

### 2. YAML soru-cevap dosyaları (`*.yaml`)

Yapılandırılmış kısa Q&A çiftleri. Sık sorulan sorular için hızlı exact-match + semantic eşleştirme.

**Şablon:**

```yaml
title: "Chiller SSS"
category: ekipman
asset_template: chiller
tags: [chiller, sss]
items:
  - q: "Chiller nedir?"
    a: "Soğutma sistemlerinde suyu veya soğutucu akışkanı ısısını alan merkezi ekipman. Tipik olarak kompresör, kondenser, evaporatör ve genleşme valfinden oluşur."
  - q: "Chiller alarm verdiğinde ne yapmalıyım?"
    a: "Önce alarmın tipini oku. Basınç alarmı ise kondenser fanını kontrol et. Sıcaklık alarmı ise refrigerant seviyesine bak. Emin değilsen teknik servisi ara."
```

Her `items[].q + a` çifti bir chunk'tır.

## Kategoriler (brief v1.5 §4.9)

- **sistem** — Otomasyon sistemi genel çalışma prensibi (Regin, Expo SCADA)
- **ekipman** — Ekipman bazlı teknik bilgi (pompa, chiller, kompresör)
- **ariza** — Arıza tipleri, olası sebepler, kontrol adımları
- **bakim** — Bakım prosedürleri, periyodik işler, yedek parçalar

## Notlar

- Dokümanlar dosya sisteminde elle eklenir/güncellenir. Dashboard üzerinden düzenleme yoktur (v1).
- Dosya eklendiğinde uygulamayı yeniden başlatmak gerekir (v1 — re-index v1.1).
- Boş dizin sorun değil — chatbot "Bu konuda bilgi tabanında bir kayıt bulamadım" cevabı verir.
- Doküman kalitesi = cevap kalitesi. Her doküman için başlığı ve kategoriyi doğru seç.
