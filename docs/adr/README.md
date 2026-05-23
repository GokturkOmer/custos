# Mimari Karar Kayıtları (ADR)

Bu dizin, Custos'un temel mimari kararlarını ve **neden** öyle verildiğini kayıt
altına alır. Bir ADR (Architecture Decision Record), tek bir önemli kararın
bağlamını, alınan kararı, sonuçlarını ve değerlendirilip seçilmeyen
alternatiflerini özetleyen kısa bir belgedir.

Amaç: Kod tabanına sonradan katılan bir geliştirici (veya dış denetçi) "burası
neden böyle yapılmış?" sorusunun cevabını koda bakmadan, tek bir yerde bulabilsin.
Kararlar zamanla değişebilir; bir karar geçersiz kaldığında ADR silinmez, durumu
"Geri Alındı" olarak güncellenir ve yerine geçen ADR'ye bağlanır.

## Format

Her ADR aşağıdaki iskeleti izler:

```markdown
# ADR-XXX: [Başlık]

**Tarih:** YYYY-MM-DD
**Durum:** Kabul | Tartışma | Reddedildi | Geri Alındı

## Bağlam
Bu karara neden ihtiyaç duyduk? Hangi kısıt veya problem vardı?

## Karar
Ne karar verdik?

## Sonuçlar
Pozitif ve negatif sonuçlar (her ikisi de yazılır).

## Alternatifler
Düşünülen ama seçilmeyen yollar ve neden seçilmedikleri.
```

## Durum açıklamaları

| Durum | Anlamı |
|---|---|
| **Kabul** | Karar yürürlükte ve kod tabanında uygulanıyor. |
| **Tartışma** | Henüz karara bağlanmadı, değerlendiriliyor. |
| **Reddedildi** | Değerlendirildi ama uygulanmadı. |
| **Geri Alındı** | Bir zamanlar geçerliydi, artık değil (yerine geçen ADR'ye bağlanır). |

## Kayıtlar

| # | Başlık | Durum |
|---|---|---|
| [ADR-001](001-two-process-architecture.md) | İki süreçli mimari (Kritik Döngü + Analitik Döngü) | Kabul |
| [ADR-002](002-read-only-modbus.md) | Sadece-okuma Modbus mimarisi | Kabul |
| [ADR-003](003-timescaledb-historian.md) | TimescaleDB tabanlı katmanlı historian | Kabul |
| [ADR-004](004-isolation-forest-not-deep-learning.md) | Anomali tespitinde Isolation Forest, derin öğrenme yok | Kabul |
| [ADR-005](005-htmx-not-spa.md) | Sunucu-merkezli arayüz (HTMX), SPA yok | Kabul |
| [ADR-006](006-isa-18.2-alarm-management.md) | ISA-18.2 alarm yönetimi prensipleri | Kabul |
| [ADR-007](007-protocol-expansion.md) | Protokol genişleme stratejisi (BACnet, OPC UA, Profinet) | Kabul (uygulama v1.1+) |

## Not

ADR-001..006 projenin ilk tasarım dönemindeki (brief v1.0–v1.7) kararları
**geriye dönük** olarak yazıya geçirir; bu nedenle yazım tarihleri ortaktır
(2026-05-22), ancak her birinin "Bağlam" bölümünde kararın hangi dönemde
alındığı belirtilir. Bundan sonra alınacak kararlar verildikleri tarihle
eklenir.

İlgili belgeler: [`docs/architecture.md`](../architecture.md) (mimarinin bütüncül
anlatımı), [`CLAUDE.md`](../../CLAUDE.md) (değişmez kurallar),
[`scripts/architecture_check.py`](../../scripts/architecture_check.py) (kuralların
otomatik denetimi).
