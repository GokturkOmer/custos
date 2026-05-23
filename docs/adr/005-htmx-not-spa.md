# ADR-005: Sunucu-merkezli arayüz (HTMX), SPA yok

**Tarih:** 2026-05-22
**Durum:** Kabul

## Bağlam

Karar proje başlangıcından (brief v1.0 stack kilidi) beri geçerlidir; bu ADR onu
yazıya geçirir.

Custos'un bir dashboard'a ihtiyacı var (canlı değerler, grafikler, alarmlar,
ayarlar), ancak bağlam bir kurumsal web uygulamasından farklı:

- **Kullanıcı:** Lokal ağda, tek tesisin teknik servisi/operatörü. Geniş eşzamanlı
  kullanıcı veya halka açık kullanım yok.
- **Geliştirici:** Tek kişi. Bir build pipeline'ı (Node/npm toolchain, bundler)
  bakım yükü demektir — CLAUDE.md "minimum hareketli parça" kuralına aykırı.
- **Sunucu zaten Python:** FastAPI + Jinja2 mevcut; ikinci bir dil/çalışma zamanı
  (JS build) eklemek maliyet.
- **Gereksinim:** Yarı gerçek-zamanlı güncelleme (canlı tag değerleri, alarm
  satırları) ve performanslı zaman serisi grafikleri.

## Karar

**Sunucu tarafında render edilen, hafif-etkileşimli bir arayüz:**

- **Jinja2** — sunucuda HTML render (sayfalar + bileşenler + partial'lar).
- **HTMX 2.0** — kısmi güncelleme (partial swap), `hx-trigger="every Ns"` ile
  periyodik canlı yenileme; ayrı bir API katmanı/SPA gerektirmez.
- **Alpine.js 3.14** — küçük, bildirimsel istemci durumu (dropdown, modal, sekme).
- **uPlot 1.6** — yüksek performanslı, hafif zaman serisi grafikleri (200 tag ×
  geniş pencere senaryosuna uygun).
- **Tailwind 3.4 standalone binary** — Node toolchain olmadan CSS; tek ikili.
- 3rd-party JS (HTMX, Alpine, uPlot) repo içinde vendored (`static/js/`); CDN
  bağımlılığı yok (lokal/çevrimdışı çalışır).

Net olarak: **build adımı yoktur** (Tailwind standalone ikili hariç). Tek dilde
(Python + şablon) geliştirilir.

## Sonuçlar

**Pozitif:**
- Build pipeline / Node bağımlılığı yok → minimum hareketli parça, basit deploy,
  düşük tek-geliştirici yükü.
- Sunucu-rendered → ilk yük basit, durum yönetimi sunucuda; istemci ince kalır.
- uPlot ile büyük zaman serisi grafikleri performanslı.
- Çevrimdışı/lokal çalışır; dış CDN'e bağımlı değil.

**Negatif:**
- Çok zengin istemci-tarafı etkileşim (karmaşık çevrimdışı-öncelikli PWA, ağır
  sürükle-bırak editörler) bir SPA kadar akıcı değildir. Custos'un ihtiyaç
  duymadığı bir zenginlik; kabul edildi.
- Canlı güncelleme HTMX polling iledir (`hx-trigger`), websocket/SSE push değil.
  Saniyeler mertebesindeki tazeleme izleme için yeterli; gerçek-zamanlı kontrol
  hedefimiz yok.

## Alternatifler

- **React/Vue SPA:** Build toolchain, Node bağımlılığı, ayrı API katmanı ve durum
  yönetimi yükü; tek geliştirici + lokal tek kullanıcı için aşırı. Reddedildi.
- **Sunucu render + vanilla JS:** HTMX'in sağladığı partial-swap ergonomisini elle
  yazmak gerekir; daha çok kod, daha çok hata. Reddedildi.
- **Tam websocket/SSE gerçek-zamanlı katman:** Ek altyapı ve hata modu; edge tek
  kullanıcı için gereksiz. Reddedildi (gerekirse v1.1+ değerlendirilebilir).
