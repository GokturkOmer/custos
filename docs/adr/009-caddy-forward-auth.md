# ADR-009: Asistan yetkilendirmesi Caddy forward_auth ile (asistanda sıfır auth kodu)

**Tarih:** 2026-05-29
**Durum:** Kabul

## Bağlam

Asistan ayrı bir süreçtir ([ADR-008](008-assistant-separate-service.md)) ama
yalnızca yetkili kullanıcılar (operator+) erişebilmeli — PDF yükleme/silme ve
sorgulama müşteri operatörünün işidir. Analitik tarafında zaten olgun bir auth
katmanı var: cookie tabanlı session + `require_operator`/`require_developer`
dependency'leri (V11-101).

Asistanın **kendi** session doğrulama/parola/rol kodunu yazması iki sorun
doğurur: (1) ikinci bir güvenlik yüzeyi ve kod tekrarı, (2) iki bağımsız auth
uygulamasının zamanla **tutarsızlaşması** (ör. rate limit, must_change_password,
session TTL davranışı). Tek bir doğru auth kaynağı tercih edilir.

## Karar

**Auth = Caddy `forward_auth`. Asistan servisinde SIFIR auth kodu.**

Caddy `/assistant/*` isteğini asistana geçirmeden ÖNCE analitik servise sorar:

```
@assistant path /assistant /assistant/*
handle @assistant {
    route {
        request_header -X-Custos-User              # (1) spoof savunması
        forward_auth 127.0.0.1:8000 {              # (2) yetki
            uri /auth/validate
            copy_headers X-Custos-User
        }
        reverse_proxy 127.0.0.1:8001               # (3) asistana geçir
    }
}
```

- **Yetki kaynağı tek:** Analitik `GET /auth/validate`, mevcut `require_operator`
  ile korunur. Geçerli operator/developer session'da **200 + `X-Custos-User`**
  döner; session yoksa 303 `/login`, yanlış rolde 403 — Caddy bunları istemciye
  iletir. Asistan hiçbir session/cookie/rol kararı vermez.
- **`X-Custos-User` = `base64url(JSON {"id":int,"username":str,"role":str})`.**
  base64url **ZORUNLU**: Türkçe kullanıcı adları (ş/ğ/ı/ö/ü/ç) non-ASCII; HTTP
  header değerleri pratikte ASCII/latin-1 ile sınırlıdır. `id`, oturumun
  **kullanıcı** kimliğidir (`Session.user_id`), session satır id'si değil.
  Asistan tarafında tek bir middleware bu header'ı `request.state.user`'a çözer
  (header yoksa/bozuksa `None`).
- **Spoof savunması (gelen header strip):** İstemci doğrudan bir `X-Custos-User`
  gönderip kimlik taklidi yapabileceğinden, forward_auth'tan ÖNCE gelen header
  **silinir**. Sıra `route` ile garanti edilir: Caddy'nin default direktif
  sırasında `request_header` (strip) `forward_auth`'tan **sonra** koşar; bu da
  forward_auth'un enjekte ettiği geçerli header'ı silerdi. `route` yazılı sırayı
  koruyarak strip → forward_auth → reverse_proxy sırasını zorlar.
- **Path-preserving `handle` (`handle_path` DEĞİL):** Asistan app yolları
  **literal** `/assistant/*` (root_path yok). `handle_path` prefix'i strip
  eder → `/assistant/health` → `/health` → 404. Bu yüzden prefix'i KORUYAN
  `handle` kullanılır; `/assistant/health` Caddy ardında da, doğrudan 8001'de de
  aynı yoldur.

## Sonuçlar

**Pozitif:**
- Tek auth kaynağı: rate limit, must_change_password, session TTL, rol mantığı
  yalnızca analitik tarafında; asistan otomatik tutarlı kalır.
- Asistan auth-free → küçük, denetlenebilir saldırı yüzeyi; güvenlik kararı
  reverse proxy katmanında merkezîleşir.
- Kimlik taklidi (header spoof) reverse proxy'de kapatılır; asistan kodu buna
  güvenmek zorunda değil.

**Negatif:**
- Asistanın yetkilendirmesi Caddy + analitik servisin ayakta olmasına bağlıdır.
  Dev'de Caddy olmadan `X-Custos-User` hiç gelmez → `request.state.user` `None`;
  bu durumda yetki kapısı da yoktur, dolayısıyla bu yalnızca **dev** kullanımıdır
  (prod'da Caddy şarttır).
- forward_auth/strip sırası bir incelik içerir; `route` ile açıkça zorlanmazsa
  sessizce yanlış davranır. (Caddyfile'da yorumla ve `route` ile sabitlendi.)

## Alternatifler

- **Asistanda kendi session doğrulaması:** Kod tekrarı + ikinci güvenlik yüzeyi
  + tutarsızlaşma riski. Reddedildi.
- **JWT / imzalı token:** Mevcut cookie-session zaten var; yeni bir token
  altyapısı gereksiz karmaşıklık. Reddedildi.
- **mTLS / ağ seviyesi kısıt:** Operator-düzeyi rol ayrımı sağlamaz; aşırı.
  Reddedildi.
- **`handle_path` + asistan `root_path="/assistant"`:** Prefix strip ile app'in
  root_path'i hizalanabilirdi; ama literal-prefix yaklaşımı Caddy ardında ve
  doğrudan 8001'de **aynı** yolu garanti eder (test/debug basitliği). Bu plan
  kararı (D); reddedilmedi ama tercih edilmedi.

## İlgili

- [ADR-008](008-assistant-separate-service.md) — asistanın ayrı süreç oluşu.
- [ADR-002](002-read-only-modbus.md) — benzer "statik/yapısal güvence" felsefesi.
