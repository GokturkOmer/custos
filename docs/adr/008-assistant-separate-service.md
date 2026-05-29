# ADR-008: Asistan ayrı servis (üçüncü bağımsız süreç)

**Tarih:** 2026-05-29
**Durum:** Kabul

## Bağlam

Pilot öncesi "demo silahı" olarak asistan modülü genişletildi: işletmenin kendi
ekipman **PDF manuellerini** yükleyip teknisyenin Türkçe/İngilizce sorusuna
**orijinal manuel sayfasını** görsel olarak döndüren, LLM'siz, %100 offline,
deterministik bir retrieval servisi (bkz. `docs/brief_v1.7.md` §4.9 ve
`docs/custos_asistan_is_plani_v1.md`).

Bu modül, sistemin geri kalanının taşımadığı ağır bağımlılıklar getirir:
`sentence-transformers` (torch backend), `faiss`, `pymupdf`, `pytesseract`,
`pdfplumber`. Bu kütüphaneler bellek/CPU dalgalanması yaratır ve uzun (saniyeler
süren) ingest/inference işleri içerir.

[ADR-001](001-two-process-architecture.md)'in kurduğu sınır nettir: Kritik
Döngü'ye ML/numerik bağımlılık sızamaz. Asistanı Analitik sürecine **gömmek**
ise — F8b'de olduğu gibi — analitik sürecinin determinizmini ve kaynak bütçesini
asistanın ağır iş yüküne açık bırakır; bir embedding/OCR işi dashboard yanıt
süresini veya arka plan analitik task'lerini etkileyebilir.

## Karar

Asistan, Kritik ve Analitik döngülerden **bağımsız ÜÇÜNCÜ bir işletim sistemi
süreci** olur:

- **Kendi portu:** `127.0.0.1:8001` (loopback; dışa açılım Caddy `/assistant/*`
  reverse proxy ile — bkz. [ADR-009](009-caddy-forward-auth.md)).
- **Kendi giriş noktası:** `python -m custos.assistant` → bağımsız FastAPI app.
  Analitik sürecinin ~13 arka plan task'inden (anomaly/kpi/spc/liveness/archive/
  watchdog/…) **HİÇBİRİ** burada çalışmaz; yalnızca PDF retrieval yaşam döngüsü.
- **Kendi systemd unit'i:** `custos-assistant.service`. sd_notify/watchdog
  katmanı YOK (analitik sürecin aksine) → `Type=simple`. Kaynak limiti cgroup
  ile: `MemoryMax=2G`, `Nice=10`, `CPUWeight=50` — asistan, kritik/analitik
  süreçleri bellek/CPU'da aç bırakamaz.
- **Kendi paket yerleşimi:** `src/custos/assistant/` (top-level; `critical`/
  `analytics`/`shared` ile kardeş).

Süreç bağımsızlığı **statik denetimle** zorlanır
([`architecture_check.py`](../../scripts/architecture_check.py), 3 kural):
`CRITICAL_ANALYTICS_IMPORT_ASSISTANT` (critical/analytics asistanı import
edemez), `ASSISTANT_IMPORTS_CRITICAL_ANALYTICS` (asistan onları import edemez) ve
`ASSISTANT_IMPORTS_SHARED_DATABASE` (DB izolasyonu — bkz.
[ADR-010](010-assistant-schema-isolation.md)).

## Sonuçlar

**Pozitif:**
- Kritik ve analitik süreçlerin determinizmi + kaynak bütçesi korunur; asistanın
  ağır kütüphaneleri ne kritik ne analitik sürece sızar.
- Asistan bağımsız çöker/yeniden başlar; analitik dashboard veya alarm üretimini
  düşürmez (ve tersi).
- `MemoryMax=2G` ile asistanın bellek tüketimi cgroup tarafından sınırlanır;
  OOM riski tek sürece hapsedilir.
- Bağımlılık ayrışması: torch/faiss/pymupdf saldırı ve bakım yüzeyi tek sürece
  izole.

**Negatif:**
- Üçüncü bir systemd unit + port + Caddy bloğu (operasyonel yüzey artar).
- Süreçler arası iletişim doğrudan import ile değil, paylaşılan PostgreSQL +
  HTTP sözleşmesi ([ADR-009](009-caddy-forward-auth.md)) üzerinden olur.

## Alternatifler

- **Analitik sürecine gömme (eski F8b):** Ağır bağımlılıklar analitik
  determinizmini ve kaynak bütçesini bozar; cgroup ile asistanı tek başına
  sınırlamak imkânsız olur. Reddedildi (bu modülün genişlemesiyle söküldü).
- **Ayrı fiziksel/sanal makine:** Tek edge mini PC için aşırı; ek donanım,
  ağ, senkron yükü. CLAUDE.md "minimum hareketli parça" ilkesine aykırı.
  Reddedildi.

## İlgili

- [ADR-001](001-two-process-architecture.md) — bu kararla iki süreçten üç
  sürece evrildi.
- [ADR-009](009-caddy-forward-auth.md) — asistanın dışa açılımı + yetkilendirme.
- [ADR-010](010-assistant-schema-isolation.md) — asistanın veri izolasyonu.
