# ADR-010: Asistan veri izolasyonu (`assistant` şeması + kendi repository)

**Tarih:** 2026-05-29
**Durum:** Kabul

## Bağlam

Asistan ayrı bir süreçtir ([ADR-008](008-assistant-separate-service.md)) ama
maliyet/operasyon nedeniyle **aynı PostgreSQL instance**'ını paylaşır (ek bir DB
sunucusu tek edge mini PC için aşırı). Asistanın kendi verisi vardır: yüklenen
PDF manuel metadata'sı, sayfa-bazlı chunk'lar (text + PNG yolu + FAISS index id
eşlemesi) ve sorgu metriği.

Sistemin DB erişim ilkesi ([ADR-001](001-two-process-architecture.md)):
`shared/database.py` tek soyut arayüz, ham SQL iş mantığına serpiştirilmez.
Ancak bu soyutlama Kritik + Analitik süreçleri içindir; asistanın onu import
etmesi süreç bağımsızlığını ([ADR-008](008-assistant-separate-service.md)) ve
veri izolasyonunu bozar — asistan, izleme tablolarına (readings, alarms, users…)
erişebilir hâle gelirdi.

## Karar

**Asistan, `public`'e HİÇ dokunmadan yalnızca kendi `assistant` PostgreSQL
şemasına, kendi data-access katmanından erişir.**

- **Kendi repository:** `src/custos/assistant/repository.py` tek SQL noktasıdır
  (CLAUDE.md mimari istisnası: asistan ayrı süreç olduğu için `shared/database.py`
  yerine kendi katmanını kullanır; yine de ham SQL tek modülde toplanır).
- **Kendi asyncpg pool'u:** `search_path=assistant`, `max_size=10`. Bağlantı
  bütçesi: 3 süreç × max 10 = ≤30 (Postgres default 100).
- **Şema izolasyonu:** Tablolar `assistant` şemasındadır (`documents`, `chunks`,
  `queries_log`). `queries_log.user_id`, `X-Custos-User`'dan gelen kullanıcı
  id'sini **yalnız metrik** olarak saklar; `public.users`'a **FK YOK** (şemalar
  arası bağımlılık ve `public`'e dokunma yasağı — [ADR-009](009-caddy-forward-auth.md)
  karar A).
- **Tek alembic zinciri:** Şema, tek migration zincirinde (numara **038**,
  head `037`) oluşturulur; ayrı bir migration başı/çatallanması yoktur.
  `custos_app` GRANT'leri `pg_roles` guard'lı (dev tek-user'da no-op, prod'da
  setup.sh deseniyle tutarlı).

İzolasyon **statik denetimle** zorlanır
([`architecture_check.py`](../../scripts/architecture_check.py)):
`ASSISTANT_IMPORTS_SHARED_DATABASE` (asistan `shared.database`'i import edemez)
ve `SQL_OUTSIDE_DATABASE` istisnası yalnızca `repository.py`'ye açıktır (SQL
başka asistan dosyasına serpiştirilemez).

## Sonuçlar

**Pozitif:**
- Asistan, izleme verisine (readings/alarms/users) **erişemez**; veri sınırı
  şema + import denetimiyle çift güvenceli.
- Bağlantı bütçesi öngörülebilir (≤30); tek instance maliyeti korunur.
- `public` şeması ile asistan şeması bağımsız evrilir; asistan migration'ları
  izleme tablolarını etkilemez.

**Negatif:**
- İki ayrı DB erişim deseni (`shared.database` + asistan `repository`) bilişsel
  yük getirir; geliştirici hangi süreçte olduğunu bilmeli.
- Şemalar arası JOIN yoktur (ör. `queries_log` ↔ `users`). Bu **bilinçlidir**:
  asistan `public`'i bilmemeli; kullanıcı kimliği yalnız metrik olarak tutulur.

## Alternatifler

- **`shared/database.py`'yi asistan tabloları için genişletmek:** Asistanı
  `public` + tüm izleme tablolarına bağlar; süreç ve veri izolasyonunu bozar.
  Reddedildi.
- **Ayrı PostgreSQL instance:** Tek edge mini PC için aşırı; ek bağlantı,
  yedekleme, sürüm yönetimi yükü. Reddedildi.
- **Asistan tablolarını `public` şemasında tutmak:** Namespace kirliliği +
  `users`'a FK koyma cazibesi (izolasyonu sızdırır). Reddedildi.

## İlgili

- [ADR-008](008-assistant-separate-service.md) — süreç bağımsızlığı.
- [ADR-009](009-caddy-forward-auth.md) — `public`'e dokunmama (karar A) ve
  `X-Custos-User` kaynağı.
- [ADR-003](003-timescaledb-historian.md) — paylaşılan PostgreSQL/TimescaleDB.
