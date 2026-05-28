# Custos Asistan Modülü — İş Planı v1

> **Tarih:** 2026-05-28
> **Kaynak:** `CUSTOS_ASISTAN_EYLEM_PLANI.md` (stratejik eylem planı) + gerçek kod tabanı analizi.
> **Karar:** Asistan modülü pilot öncesi **demo silahı** olarak 1. önceliğe alındı (AVM pilotu ≥2 ay ertelendi). KOSGEB 2. plana çekildi; asistan demosu İş Geliştirme (Eyl-Kas, 10 dk video) dosyasını besleyecek.
> **Brief tanımı:** `docs/brief_v1.7.md` §4.9 (genişletildi). **Mimari kurallar:** `CLAUDE.md` (3 süreç + asistan istisnaları).

---

## 0. Verilen kararlar (2026-05-28)

| # | Karar | Seçim |
|---|---|---|
| 1 | Zamanlama | Asistan modülü ŞİMDİ 1. öncelik; KOSGEB 2. plana |
| 2 | Mimari | **Ayrı servis** — Critical / Analytics / Asistan üç bağımsız süreç |
| 3 | Persistence | **PostgreSQL `assistant` şeması** + diske kalıcı FAISS + PNG (MD eylem planındaki gibi) |
| 4 | UI | **Mevcut sohbet sayfası değiştirilecek** → görsel arama UI |

**Onaylı yeni bağımlılıklar (2026-05-28):** `pymupdf`, `pytesseract`, `rank_bm25`, `pdfplumber`. (`sentence-transformers` + `faiss-cpu` zaten kurulu.)

---

## 0.1. Kilitli kararlar — Faz 0 (2026-05-29, KİLİTLİ)

Faz 0 uygulamasında aşağıdaki kararlar tartışmaya kapalıdır:

- **A) Auth = Caddy forward_auth (asistanda SIFIR auth kodu).** Caddy `/assistant/*`
  isteğini geçirmeden önce analytics'e sorar (`GET /auth/validate`,
  `require_operator`). Analytics geçerli session'da 200 + `X-Custos-User` header
  döner = **base64url(JSON `{"id":int,"username":str,"role":str}`)**. base64url
  ZORUNLU (Türkçe kullanıcı adları non-ASCII). Asistan `public` şemasına HİÇ
  dokunmaz; bir middleware `X-Custos-User`'ı parse edip `request.state.user`'a
  yazar (header yoksa `None`). Caddy/forward_auth wiring **Bölüm 2'de**; Bölüm 1
  yalnızca middleware + parse.
- **B) DB izolasyonu.** Asistan kendi `repository.py` (kendi asyncpg pool, yalnızca
  `assistant` şeması search_path). Ham SQL tek noktada. Bağlantı bütçesi: 3 süreç ×
  max 10 = ≤30 (Postgres default 100).
- **C) Migration = TEK alembic zinciri, numara 038** (head `037_mode_aware_spc`;
  `026` zaten `retention_config`).
- **D) UI = odaklı araç + paylaşılan tasarım.** Kendi template'leri + paylaşılan
  CSS token; üstte "Custos · Asistan" bar + "← Panele dön" linki; sol nav YOK.
  **Eski F8b sohbet UI silinir** (route + template + test). Tam görsel UI Faz 3;
  Bölüm 1'de yalnızca placeholder index + bar.
- **E) Upload/silme = operator+** (forward_auth `require_operator` ile garanti).
- **F) OCR = doğruluk-max hibrit.** Gömülü PDF text HER ZAMAN birincil; OCR yalnızca
  text'i az/yok sayfada (300 DPI, `tur+eng`, merge). Orijinal PDF saklanır. Ekran
  render PNG = 200 DPI. (Uygulama Faz 1; Bölüm 1'de yalnızca config alanları.)
- **G) Embedding modeli:** `paraphrase-multilingual-MiniLM-L12-v2`, thread-safe lazy
  singleton, FAISS `IndexFlatIP` cosine (mevcut F8b altyapısı, olduğu gibi taşınır).
- **H) LLM yok, deterministik** (kaynak gösterir, cevap üretmez).

**Faz 0 iki bölüme ayrıldı:**
- **Bölüm 1 (bu plan kapsamı):** kod omurgası + DB — paket taşıma, F8b silme,
  config, bağımsız FastAPI servis + `X-Custos-User` middleware, migration 038 +
  repository iskeleti, architecture_check sınır kuralları.
- **Bölüm 2 (ayrı oturum):** Caddy forward_auth wiring + `custos-assistant.service`
  systemd unit + setup.sh dizinleri + ADR `008/009/010`.

---

## 1. Neyin hazır / neyin yeni (reuse haritası)

F8b asistan modülü zaten var ve test edilmiş — sıfırdan başlamıyoruz. Mevcut kod `src/custos/analytics/assistant/`:

| Bileşen | Mevcut durum | Bu planda |
|---|---|---|
| `embeddings.py` | ✅ Hazır — model **birebir** plandaki (`paraphrase-multilingual-MiniLM-L12-v2`), thread-safe lazy singleton | **Olduğu gibi taşı** |
| `index.py` | ✅ FAISS `IndexFlatIP` build/search; **persist yok** (yorumda v1.1'e ertelenmiş) | **Taşı + diske persist EKLE** + ölçek için opsiyonel HNSW |
| `retriever.py` | ✅ YAML exact-match + semantic + skor eşiği; TR-ASCII normalize | **Genişlet:** BM25 + RRF hibrit ekle |
| `service.py` | ✅ Singleton + rebuild + `Depends` wiring | **Taşı + uyarla** (ayrı servis) |
| `loader.py` | ✅ Markdown/YAML chunker | **Korunur** (dahili KB) + yanına `pdf_loader.py` (YENİ) |
| Dashboard route'ları (`app.py:5050`) | ✅ Sohbet sayfası + KB CRUD | **Analytics'ten kaldır**, asistan servisine taşı |
| Templates (chat) | ✅ `assistant.html` + mesaj partial | **Değiştir** → görsel arama UI |
| 5 test dosyası | ✅ retriever/index/loader/routes/smoke | **Taşı + genişlet** |
| Config (`config.py:115`) | ✅ 4 ayar | **Genişlet** (PDF/servis ayarları) |
| `assistant` PG schema | ❌ Yok (F8b dosya-tabanlı, DB kullanmıyor) | **YENİ** (migration 038) |
| PDF ingest + OCR | ❌ Yok | **YENİ** (Faz 1) |
| BM25 / RRF | ❌ Yok | **YENİ** (Faz 2) |
| Görsel UI (thumbnail/modal/highlight) | ❌ Yok | **YENİ** (Faz 3-4) |
| Ayrı servis (entrypoint, systemd, Caddy) | ❌ Yok (analytics içinde) | **YENİ** (Faz 0) |

**Özet:** semantic retrieval altyapısının ~%40'ı hazır. Asıl yeni iş: PDF→sayfa-görsel pipeline, kalıcılık, hibrit arama, görsel UI ve süreç ayrımı.

---

## 2. Hedef mimari

### 2.1. Üç süreç

```
┌─────────────┐   ┌──────────────┐   ┌────────────────┐
│ Critical    │   │ Analytics    │   │ Asistan        │
│ (collector  │   │ (dashboard,  │   │ (PDF retrieval)│
│  +threshold)│   │  ML, archive)│   │                │
│ port —      │   │ port 8000    │   │ port 8001      │
└─────────────┘   └──────────────┘   └────────────────┘
       │                  │                   │
       └──────────────────┴───────────────────┘
                          │
                   PostgreSQL (tek instance)
              public schema        assistant schema
```

Caddy: `/` → 8000 (dashboard), `/assistant/*` → 8001 (asistan). systemd: ayrı `custos-assistant.service` (`MemoryMax=2G`, `Nice=10`, `CPUWeight=50`).

### 2.2. Paket yerleşimi — `src/custos/assistant/` (top-level promote)

Asistan, `analytics`'in altından çıkıp `critical`/`analytics`/`shared` ile kardeş top-level pakete taşınır (gerçek süreç ayrımı için):

```
src/custos/assistant/
  __init__.py
  __main__.py          # uvicorn entrypoint (port 8001) — YENİ
  app.py               # bağımsız FastAPI app + route'lar — YENİ
  repository.py        # assistant schema data-access (tek SQL noktası) — YENİ
  pdf_loader.py        # pymupdf ingest + OCR + PNG render — YENİ
  highlighter.py       # sayfa highlight (pymupdf draw_rect) — YENİ
  loader.py            # (taşındı) md/yaml chunker — dahili KB için korunur
  embeddings.py        # (taşındı) — değişmez
  index.py             # (taşındı) + disk persist
  retriever.py         # (taşındı) + BM25/RRF
  service.py           # (taşındı) + uyarla
  templates/           # asistan servisine ait template'ler
  static/              # (gerekiyorsa) thumbnail/modal JS
```

### 2.3. Depolama

- PNG'ler: `/var/lib/custos/assistant/pages/{document_id}/{page_no}.png`
- Highlight cache: `/var/lib/custos/assistant/highlights/{document_id}/{page_no}_{query_hash}.png`
- FAISS index: `/var/lib/custos/assistant/index.faiss` (+ chunk_id ↔ faiss_id eşlemesi DB'de)
- Kaynak PDF: `/var/lib/custos/assistant/sources/{document_id}.pdf`
- `setup.sh`: dizin + `chown custos` + `chmod 750` (Parquet arşiv deseni ile aynı).

---

## 3. Veri modeli — `assistant` PostgreSQL şeması

`repository.py` tek SQL noktası (CLAUDE.md mimari istisnası: asistan servisi kendi data-access katmanını kullanır, ham SQL serpiştirilmez).

```sql
CREATE SCHEMA IF NOT EXISTS assistant;

CREATE TABLE assistant.documents (
  document_id   SERIAL PRIMARY KEY,
  filename      TEXT NOT NULL,
  equipment_model TEXT,
  equipment_type  TEXT,        -- chiller/ahu/pump/cooling_tower/boiler/vfd/other
  language        TEXT,        -- tr/en/mixed
  total_pages   INT,
  ocr_used      BOOLEAN DEFAULT FALSE,
  source_pdf_path TEXT,
  uploaded_at   TIMESTAMPTZ DEFAULT NOW(),  -- UTC (CLAUDE.md)
  uploaded_by   TEXT
);

CREATE TABLE assistant.chunks (
  chunk_id      SERIAL PRIMARY KEY,
  document_id   INT REFERENCES assistant.documents(document_id) ON DELETE CASCADE,
  page_no       INT,
  text_content  TEXT,
  png_path      TEXT,
  section_title TEXT,
  faiss_index_id INT UNIQUE,
  has_table     BOOLEAN DEFAULT FALSE,
  has_figure    BOOLEAN DEFAULT FALSE
);
CREATE INDEX idx_chunks_document  ON assistant.chunks(document_id);
CREATE INDEX idx_documents_equipment ON assistant.documents(equipment_type, equipment_model);

CREATE TABLE assistant.queries_log (
  query_id        SERIAL PRIMARY KEY,
  query_text      TEXT,
  result_chunk_ids INT[],
  selected_chunk_id INT,
  query_time_ms   INT,
  asked_at        TIMESTAMPTZ DEFAULT NOW(),  -- UTC
  user_id         INT
);
```

> Karar (2026-05-29, KİLİTLİ): `queries_log.user_id` için `public.users` FK'ı
> KOYULMADI. Asistan `public` şemasına dokunmaz (karar A); `user_id` yalnızca
> metrik amaçlı `X-Custos-User`'dan gelen `id`'yi saklar (şemalar arası FK ve
> public bağımlılığı yok).

**`repository.py` metotları (asgari):** `insert_document`, `insert_chunks_batch`, `get_chunks_by_faiss_ids`, `list_documents`, `delete_document`, `log_query`, `mark_selected_chunk`.

---

## 4. Fazlar

> Her faz sonunda **çalışan bir parça** olsun (incremental). Her yeni fonksiyona ≥1 test (CLAUDE.md). Her faz sonunda `ruff check .` + `mypy src/` temiz.

### Faz 0 — İskelet + süreç ayrımı (≈3-4 gün)

**Hedef:** Ayrı çalışan boş asistan servisi + DB şeması + paket taşıma. İki bölüm
(bkz. §0.1): **Bölüm 1** = kod omurgası + DB; **Bölüm 2** = auth wiring + deploy + ADR.

**Bölüm 1 — kod omurgası + DB:**
- [ ] `pyproject.toml`: `pymupdf`, `pytesseract`, `rank_bm25`, `pdfplumber` ekle (+ mypy `ignore_missing_imports` override'ları). Onay alındı.
- [ ] Paket taşıma: `analytics/assistant/*` (6 dosya) → `assistant/*` (top-level). Paket-içi import'lar relative kalır; `custos.analytics.assistant.*` → `custos.assistant.*`.
- [ ] **F8b sil:** analytics dashboard'dan asistan + knowledge route'ları, import'lar, nav link'i ve eski sohbet/KB template'lerini DOĞRUDAN sil (production yok). `settings.html` KB kartı kaldırılır. `custos.analytics.assistant` referansı SIFIR.
- [ ] `assistant/app.py`: bağımsız FastAPI app (`root_path` ile `/assistant` altında). `assistant/__main__.py`: uvicorn entry (127.0.0.1:8001, port config'ten). Analytics'in ~13 background task'inden HİÇBİRİ alınmaz.
- [ ] **Auth (karar A):** Caddy forward_auth → `X-Custos-User` base64url(JSON) parse middleware → `request.state.user` (header yoksa `None`; dev'de Caddy'siz de test edilebilir). Asistanda başka auth kodu YOK.
- [ ] `/assistant/health` (JSON 200, auth gerektirmez) + placeholder index ("Custos · Asistan" bar + "← Panele dön").
- [ ] Migration `038_create_assistant_schema.py`: `assistant` şeması + `documents`/`chunks`/`queries_log` + `custos_app` GRANT'leri (rol varsa; `pg_roles` guard'lı — dev tek-user'da no-op). Rollback drill yeşil.
- [ ] `assistant/repository.py` iskeleti: kendi asyncpg pool (`assistant` search_path) + metot imzaları (gövdeler Faz 1).
- [ ] `scripts/architecture_check.py`: `SQL_OUTSIDE_DATABASE` exclude'a repository ekle + 3 sınır kuralı (critical/analytics↔assistant import yasağı, assistant→`shared.database` yasağı).
- [ ] Config: `custos_assistant_*` yeni alanlar (port, data_dir, render/ocr DPI, ocr_min_chars) + `.env.example` senkron.
- **Çıktı:** `python -m custos.assistant` → 8001'de ayağa kalkan placeholder servis; analytics 8000 etkilenmemiş; ruff+mypy+architecture_check+pytest yeşil.

**Bölüm 2 — auth wiring + deploy + ADR (ayrı oturum):**
- [ ] Caddy `/assistant/*` → 8001 + forward_auth → analytics `/auth/validate` (`X-Custos-User` üretimi analytics tarafında).
- [ ] `deploy/custos-assistant.service` (cgroup `MemoryMax=2G`, `Nice=10`, `CPUWeight=50`) + setup.sh dizinleri (`/var/lib/custos/assistant/`) + `assistant` schema GRANT entegrasyonu.
- [ ] ADR `008` (ayrı servis), `009` (forward_auth), `010` (assistant schema izolasyonu).

### Faz 1 — PDF ingest pipeline (≈2 hafta — en büyük yeni parça)

**Hedef:** PDF yükle → text + sayfa PNG diskte, metadata + chunk'lar DB'de.

- [ ] `pdf_loader.py`: `pymupdf` ile sayfa-bazlı text extraction + sayfa PNG render (200 DPI).
- [ ] OCR tespiti: sayfada text yoksa (taranmış) `pytesseract` fallback (TR+EN dil paketleri). `ocr_used` işaretle.
- [ ] `pdfplumber` fallback (pymupdf belirli formatta çökerse) — try/except.
- [ ] `has_table`/`has_figure` tespiti (pymupdf `get_text("dict")` blok analizi — basit heuristik).
- [ ] Chunk üretimi: bir sayfa = bir chunk (page_no + png_path + text_content). `repository.insert_*` ile DB'ye yaz.
- [ ] Equipment metadata: yükleme formu alanları (type/model/language) → `documents`.
- [ ] Test: 3-5 farklı manuel (digital + scanned) ile ingest; doğru sayfa sayısı + text + PNG.
- **Çıktı:** API/CLI ile PDF yükle → sayfalar diskte, metadata DB'de.

### Faz 2 — Retrieval: hibrit + kalıcı (≈4-5 gün — büyük kısmı reuse)

**Hedef:** Sorgu at → doğru sayfa(lar) gelir.

- [ ] `embeddings.py` → değişmeden kullan.
- [ ] `index.py` → **diske persist ekle** (`write_index`/`read_index`); başlangıçta DB'den chunk'ları yükle, index'i diskten oku (yoksa rebuild). Ölçek: 10+ manuel × yüzlerce sayfa için `IndexHNSWFlat` opsiyonu değerlendir.
- [ ] BM25 katmanı: `rank_bm25.BM25Okapi` corpus build (kod numarası araması için: "E102"). Yeni `bm25.py` veya `retriever.py` içine.
- [ ] RRF birleştirme (~30 satır): dense top-K + sparse top-K → reciprocal rank fusion.
- [ ] Equipment-aware filtre: önce metadata filtrele (type/model), sonra ara.
- [ ] `retriever.py`: `answer()` yerine/yanında `search()` → top-3 sayfa chunk (görsel UI için).
- [ ] `POST /assistant/search` endpoint + `queries_log` kaydı (query_time_ms).
- [ ] Test: ~30 örnek sorgu ile retrieval accuracy; RRF'nin kod-numarası recall'ünü artırdığını göster.
- **Çıktı:** Arama API'si → doğru sayfa(lar) skorlu döner, kalıcı index.

### Faz 3 — Görsel UI (mevcut sayfa değişimi) (≈1.5 hafta)

**Hedef:** Web arayüzünden tam fonksiyonel görsel kullanım.

- [ ] `assistant.html` (sohbet) → **görsel arama** sayfasına dönüştür: arama kutusu + ekipman filtre dropdown.
- [ ] Sonuç UI: 3 sayfa thumbnail + metin önizleme (HTMX partial). Eski `assistant_message.html` partial'ı kaldır.
- [ ] Tıklayınca tam sayfa modal: zoom in/out, sayfa no, kaynak PDF adı (Alpine.js).
- [ ] Upload sayfası: drag-drop + progress bar + metadata formu (mevcut `bulk_import.py` deseni referans).
- [ ] Yüklenen dokümanlar listesi: sil, metadata düzenle.
- [ ] HTMX polling ile büyük PDF yükleme ilerlemesi (async ingest).
- **Çıktı:** Web arayüzünden yükle → ara → sayfa gör, tam akış.

### Faz 4 — Sayfa içi highlight (premium UX) (≈3-4 gün)

**Hedef:** Sayfa açıldığında ilgili kısım sarıyla vurgulu.

- [ ] `highlighter.py`: pymupdf `page.get_text("dict")` ile cümle koordinatları (bbox).
- [ ] Sorguya en yakın cümle(ler)in bbox'larını hesapla (embedding benzerliği cümle bazında).
- [ ] pymupdf **native** `page.draw_rect` ile yarı saydam sarı dikdörtgen (PIL gerekmeyebilir).
- [ ] Highlight cache: `{document_id}/{page_no}_{query_hash}.png` (re-render yapma).
- **Çıktı:** Sonuç sayfasında sorguya en alakalı bölge highlight'lı.

### Faz 5 — Test, polish, hardening + demo paketi (≈1 hafta)

- [ ] 50+ sorgu ile gerçek accuracy testi; `queries_log` doldurma.
- [ ] Edge case: çok dilli PDF, sadece görsel sayfa, çok büyük PDF (500+ sayfa).
- [ ] Performans: 10 PDF × ~1000 sayfa toplam → arama < 1 sn. Cache warm-up.
- [ ] systemd hardening (memory limit, restart policy), structlog olay kapsamı.
- [ ] **Demo manuelleri** (Göktürk paralel topladı): chiller EN + kombi/kazan TR + VFD. 5-6 demo sorgusu.
- [ ] Demo akışı provası: yükle → sorgu → highlight → **interneti kapat** → aynı sorgu (offline kanıtı).
- **Çıktı:** Production-ready asistan v1.0 + saha demo paketi.

---

## 5. CLAUDE.md uyum kontrol listesi

- [ ] Yorumlar/commit/docstring **Türkçe**; değişken/fonksiyon İngilizce.
- [ ] `datetime.now(timezone.utc)` — `uploaded_at`/`asked_at` UTC; UI'da yerele çevir.
- [ ] DB erişimi `repository.py` tek noktada (ham SQL iş mantığına serpiştirilmez — asistan istisnası).
- [ ] `print` yok → `structlog`.
- [ ] Type hint zorunlu (mypy strict); her yeni fonksiyona ≥1 test.
- [ ] Critical/Analytics loop'larına ML/PDF bağımlılığı SIZMAZ (yalnızca asistan servisi).
- [ ] `.env` dokunma; `pyproject.toml`/`docker-compose.yml` değişikliği onaylı (deps onaylandı).
- [ ] Embedding modeli yalnızca inference (cihazda eğitim yok).

---

## 6. Riskler ve fallback'ler (kod gerçeğiyle revize)

| Risk | Etki | Fallback |
|---|---|---|
| OCR Türkçe kalitesi düşük | Orta | Demo manuelleri dijital seç; mevcut TR-ASCII normalize + BM25 katmanı |
| Embedding endüstriyel jargon zayıf | Orta | BM25 (kod no) + equipment metadata filtresi; F8b'deki exact-match deneyimi |
| **Ayrı servis auth karmaşası** | **Çözüldü** | Karar A: Caddy forward_auth → analytics `require_operator`; asistan yalnızca `X-Custos-User` base64url parse eder (sıfır auth kodu) |
| pymupdf belirli PDF'de çöker | Düşük | `pdfplumber` fallback (try/except) |
| FAISS index disk corruption | Yüksek | DB'den rebuild script (her PDF baştan index'le); günlük backup |
| Kalıcı index ölçek (binlerce sayfa) | Orta | `IndexHNSWFlat` + lazy load; pilot ölçeğinde flat yeterli |
| Paket taşıma mevcut 5 testi kırar | Orta | İlk iş import güncelleme + test yeşili; sonra yeni özellik |
| Asistan demo Custos ana ürün algısını gölgeler | Düşük | Demo'da ana ürünü mutlaka göster; asistanı **giriş kapısı** konumla |

---

## 7. Tahmin özeti

| Faz | İş | Tahmin |
|---|---|---|
| 0 | İskelet + süreç ayrımı + schema | 3-4 gün |
| 1 | PDF ingest + OCR | ~2 hafta |
| 2 | Hibrit + kalıcı retrieval | 4-5 gün |
| 3 | Görsel UI | ~1.5 hafta |
| 4 | Highlight | 3-4 gün |
| 5 | Test + demo paketi | ~1 hafta |

**Toplam:** ~6-8 hafta odaklı çalışma (reuse sayesinde eylem planındaki 8-10 haftadan kısa). **Gerçekçilik notu:** tek kişi + paralel cepheler (KOSGEB kalemleri, validation PDF, LinkedIn, yan iş) bu süreyi uzatabilir; "haftada 1 karar günü, kalan günler execute" disiplini önerilir.

**Başlangıç sırası:** Faz 0 → Faz 1 → Faz 2 → Faz 3 → Faz 4 → Faz 5. Her faz bağımsız demolanabilir; minimum demo için Faz 4 (highlight) atlanıp Faz 3 sonunda da sunum yapılabilir.
