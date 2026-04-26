# Custos v1.1 — Pilot Öncesi Sertleştirme + Olgunlaşma Planı

**Versiyon:** 1.1 (plan)
**Hazırlayan:** Göktürk + Claude (24-26 Nis 2026 konuşması)
**Hazırlık tarihi:** 26 Nisan 2026
**Pilot kurulum:** 5 Haziran 2026 (Torunlar GYO AVM)
**Pilot kabul testi:** 5 Temmuz 2026
**Kapsam:** Pilot öncesi sertleştirme (Faz 1) + pilot süreci (Faz 2) + kabul sonrası olgunlaşma (Faz 3) + v1.0.1 tech debt backlog 8 kalem (Faz 0)
**Kanonik dökümanlar:** `CLAUDE.md`, `docs/brief_v1.7.md`, `docs/custos_is_plani_v1.md`, `docs/pilot_denetim_plani_v1.md`

---

## 0. Özet

Bu plan üç farklı kaynaktan gelen kalemleri tek bir akışta toparlar:

1. **Memory v1.0.1 tech debt backlog (8 kalem)** — endurance dry-run sırasında ertelenmiş işler.
2. **Gap analizi (26 Nis 2026)** — Claude.ai mimari konuşmasından çıkan güvenlik + ML mimarisi önerileri.
3. **Karar konuşması (26 Nis 2026)** — 12 mimari kararın netleştirildiği oturum.

Plan dört faza bölünmüştür:

- **Faz 0** — Memory v1.0.1 backlog 8 kalem (pilot bağımsız, çoğu dokümantasyon/v1.1 ML)
- **Faz 1** — Pilot öncesi sertleştirme (5 Haz öncesi 6 hafta, ~10-14 iş günü efor)
- **Faz 2** — Pilot süreci (5 Haz – 5 Tem, saha + hata düzeltme + eğitim)
- **Faz 3** — Pilot kabul sonrası olgunlaşma (Tem 2026+, 3-6 ay)
- **Faz 4** — v1.2+ uzun vade (vizyon)

Toplam pilot şart kalemi: **14 madde**. Toplam iş günü tahmini Faz 1: **10-14 iş günü** (1.5-2 hafta net efor, 6 hafta takvim — geniş buffer).

---

## 1. Karar Kayıtları (26 Nis 2026 konuşması)

İş planı bu kararlar üzerine kuruludur. Sonradan değişirse versiyon artırılır.

| # | Karar | Detay |
|---|---|---|
| K1 | **Roller**: 2 rol — Operator + Developer (sen) | Müşteri tarafında ayrı "Admin" rolü yok. Operator = müşteri yetkilisi |
| K2 | **Bakım modu**: Operator + Developer her ikisi global ve sınırsız bakım moduna alabilir | Pilot için pratik tercih |
| K3 | **Push abonelik ekleme**: Operator kendi cihazını ekleyebilir | Self-service kayıt |
| K4 | **Backup**: Hibrit C — haftalık tam pg_dump (1 ay/4 dosya) + günlük JSON config snapshot | Felaket kurtarma + konfigürasyon takibi |
| K5 | **Audit log retention**: Sınırsız | Disk yükü ihmal edilebilir, denetlenebilirlik öncelikli |
| K6 | **Knowledge base saklama**: Hibrit — temel dokümanlar Git'te, özel/saha-spesifik dokümanlar `/var/custos/knowledge/local/` | Versiyon kontrolü + esneklik |
| K7 | **Mini PC**: Monitörsüz çalışır; tüm erişim dashboard üzerinden | Kiosk modu yok |
| K8 | **Hostname**: IT'nin atadığı statik IP | mDNS değil. Self-signed sertifika IP'ye bağlı |
| K9 | **Anomaly model retraining**: Otomatik haftalık → shadow mode'a düşer, sen manuel onaylarsın | Operatör shadow log'u görmez |
| K10 | **Severity tier**: 4 katman (info / warn / crit / emergency) | Emergency kullanıcı tarafından threshold formunda seçilir, hysteresis bypass |
| K11 | **Stuck-at**: Hibrit — Layer 1 kural (pilot gün 1) + Layer 3 ML personalize (2 hafta sonra opsiyonel) | Açıklanabilirlik + adaptasyon |
| K12 | **TLS**: Self-signed sertifika + caddy ters-proxy | İnternet bağlantısı gerekmez, browser TOFU |
| K13 | **Watchdog**: İç (3 katman: systemd + cross-service heartbeat + dashboard widget) | Dış watchdog yok, internet kapalı |
| K14 | **DB user ayrımı**: custos_app (runtime) + custos_admin (migration) | Blast radius küçültme |
| K15 | **Test/bakım modu**: Per-instance + global, manuel + sabit süreler (1h/4h/12h/24h/3g/manuel) | Süre dolunca otomatik kapanır |
| K16 | **Anomaly etiketleme**: 4 sınıf (Gerçek / Yanlış / Bakım / Bilinmiyor) | "Bilinmiyor" şart, baskı yok |

---

## 2. Faz 0 — Memory v1.0.1 Tech Debt Backlog (8 kalem)

Pilot bağımsız. Faz 3'e (kabul sonrası) yayılabilir, bazıları Faz 1'le birlikte kapatılabilir.

### V11-000-A — README PAT Rehber (Memory kalem 2)

- **Faz**: 1 (pilot öncesi, dokümantasyon kolaylığı için)
- **Süre**: 30 dk - 1 saat
- **Bağımlılık**: yok
- **Gerekçe**: Pilot mini PC'de `git pull` için Personal Access Token ayarı. Repo private, saha güncellemesi için lazım.
- **Kapsam**: `deploy/README_PILOT.md`'ye yeni bölüm — PAT oluşturma, `.git/config` veya `~/.netrc` ayarı, expire date hatırlatma.
- **Deliverable**: README ek bölüm.
- **Test**: Yeni Ubuntu'da PAT ile `git pull` denenir.

### V11-000-B — Collector tick_miss Gerçek İç Sayacı (Memory kalem 3)

- **Faz**: 3 (pilot kabul sonrası)
- **Süre**: 2-3 saat
- **Bağımlılık**: yok
- **Gerekçe**: Şu an metric daemon journal'dan batch_count proxy'siyle ölçüyor. Gerçek iç sayaç collector.py'de tick döngüsü zaman ölçümünden gelmeli. Daha doğru telemetri.
- **Kapsam**: `collector.py:_run_tick` içinde `target_interval - elapsed` ölçümü, miss varsa `_tick_miss_count++`. Property exposure.
- **Deliverable**: `total_tick_count`, `tick_miss_count`, `tick_miss_ratio` property'leri (ratio zaten var, miss_count yok).
- **Test**: 1 unit test — yapay slow tick simüle, miss sayısı artar mı.

### V11-000-C — DatabaseInterface.transaction() Context (Memory kalem 4)

- **Faz**: 3
- **Süre**: 4-6 saat
- **Bağımlılık**: yok
- **Gerekçe**: Atomik birden fazla operasyon için context manager. Pratik fayda: dashboard'da threshold + alarm_event birlikte yazılırsa biri patlarsa diğeri rollback.
- **Kapsam**: `shared/database.py:DatabaseInterface` abstract metoda `transaction()` async context manager eklenir. TimescaleDB impl'inde asyncpg'in `connection.transaction()` wrap'lenir. 4-5 caller refactor.
- **Deliverable**: Yeni interface metodu + 1 implementasyon + 4-5 caller migration.
- **Test**: 2 integration test — başarılı commit + rollback senaryoları.

### V11-000-D — Pyproject Whitelist Audit (Memory kalem 5)

- **Faz**: 3
- **Süre**: 1-2 saat
- **Bağımlılık**: yok
- **Gerekçe**: A1 architecture_check.py kod düzeyinde. Paket düzeyinde "yalnızca whitelisted paketler kurulu mu" kontrolü yok.
- **Kapsam**: `scripts/whitelist_audit.py` — `pip list` JSON çıktısı vs `pyproject.toml [project.dependencies]` whitelist karşılaştırması. Beklenmeyen paket varsa uyarı.
- **Deliverable**: Yeni script + CI step (architecture_check sonrası).
- **Test**: Bilinçli unauthorized paket simülasyonu — fail eder mi.

### V11-000-E — Torch Upgrade (Memory kalem 12)

- **Faz**: 3 (transformers ile birlikte değerlendir)
- **Süre**: Yarım gün
- **Bağımlılık**: V11-000-H (transformers)
- **Gerekçe**: Şu an `torch==2.11.0+cpu` pinli. transformers 5.x ile birlikte değerlendirilmeli.
- **Kapsam**: `pyproject.toml` torch pin güncelle, sentence-transformers uyumluluk testi, setup.sh kontrol.
- **Deliverable**: pyproject güncelleme + setup.sh güncelleme + smoke test.
- **Test**: `pytest tests/unit/test_assistant_*` yeşil kalır mı.

### V11-000-F — AVM Knowledge Base İçeriği (Memory kalem 19c)

- **Faz**: 1 (pilot öncesi şart — pilot kabul kriteri "1 doğru cevap")
- **Süre**: 3-7 iş günü (yazım + technical review)
- **Bağımlılık**: V11-110 (knowledge base hibrit yapısı)
- **Gerekçe**: Asistan modülü kod hazır ama içerik boş. Pilot başarı kriteri: chatbot ≥1 teknik soruya doğru cevap. Boş knowledge base = "bilgi bulamadım" cevabı.
- **Kapsam**: 20-30 Markdown doküman:
  - 9 AVM template ekipmanı için temel kılavuz (chiller, AHU, FCU, cooling tower, booster pump, lift station fresh/waste, energy meter, circulation pump)
  - Her ekipman için: çalışma prensibi, kritik parametreler ve normal aralıklar, sık alarmlar + müdahale, troubleshooting akışı
  - YAML Q&A: sık sorulan 30-50 soru (exact match için)
- **Kaynaklar**:
  - Üretici teknik kılavuzları (Trane, Carrier, York, Daikin, Wilo, Grundfos, Regin)
  - ASHRAE Handbook (HVAC Applications)
  - TMMOB MMO kılavuzları
  - Pilot kurulum sırasında saha tecrübesi (Torunlar GYO teknik servis röportaj)
- **Deliverable**: `data/knowledge/` altında 20-30 .md + 1-2 .yaml dosyası.
- **Test**: 10 örnek soru sor → en az 8 doğru cevap, threshold 0.60.

### V11-000-G — Instance-Level Auto Chart (Memory kalem 23)

- **Faz**: 3
- **Süre**: 1-2 iş günü
- **Bağımlılık**: yok
- **Gerekçe**: Tag eklendiğinde overview'da otomatik chart slot oluşma UX iyileştirmesi.
- **Kapsam**: Yeni tag activation event'inde, asset_instance binding'i varsa default chart slot oluştur (tag_id ile pre-seed). Settings opsiyonu (auto_chart_on_bind: bool).
- **Deliverable**: Dashboard route güncellemesi + 4-5 test.
- **Test**: Yeni binding → overview'da yeni chart panel beklenir.

### V11-000-H — Transformers RC Upgrade (CVE-2026-1839)

- **Faz**: 3 (sentence-transformers 4.x stable beklemek mümkün)
- **Süre**: 1-2 iş günü
- **Bağımlılık**: yok (RC sürümün stable olmasını bekleyebilir)
- **Gerekçe**: CVE-2026-1839 orta seviye. transformers 5.x sentence-transformers 4.x ister; şu an `<4` pin.
- **Seçenekler**:
  1. **A**: sentence-transformers 4.x stable bekle, çıkınca uyumlu olarak upgrade
  2. **B**: Alternatif embedding (örn. `text-embeddings-inference` standalone, ya da fastText) — büyük rework
  3. **C**: CVE'nin gerçek etki potansiyelini değerlendir, pin gevşetme tercih
- **Kapsam**: A seçeneği önerilir. Stable çıkınca pyproject güncelleme + smoke test.
- **Deliverable**: pyproject güncelleme + assistant smoke test yeşil.

---

## 3. Faz 1 — Pilot Öncesi Sertleştirme (5 Haz öncesi)

**Toplam**: 14 kalem, ~10-14 iş günü efor, 6 hafta takvim. Her kalem bağımsız test edilebilir; küçük PR akışı.

### Sıralı bağımlılık zinciri (önemli)

```
V11-101 (Auth + 2 rol)
    ├─→ V11-102 (TLS + caddy)
    ├─→ V11-103 (Push çoklu alıcı + UI)
    └─→ V11-104 (Test/bakım modu UI)
V11-105 (Watchdog) — bağımsız
V11-106 (DB user ayrımı) — bağımsız
V11-107 (Severity 4-tier) — push'tan önce iyi
V11-108 (Stuck-at Layer 1) — bağımsız
V11-109 (Backup hibrit C) — bağımsız
V11-110 (Knowledge base hibrit yapı) → V11-000-F (içerik)
V11-111 (Resource alarm) — V11-105 sonrası
V11-112 (PG localhost-only + SSH disable + LUKS runbook) — bağımsız
V11-113 (NTP doğrulama + healthcheck genişletme) — bağımsız
```

---

### V11-101 — Auth + 2 Rol (Operator/Developer)

- **Faz**: 1
- **Sıra**: 1 (en önce — diğer her şeyin temeli)
- **Süre**: 1 iş günü (8 saat)
- **Bağımlılık**: yok
- **Gerekçe (K1)**: Memory'de "0 auth" — pilot kritik açık. Pilot mini PC dashboard'a LAN'daki herkes erişip threshold silebilir. 2 rol ile operatör yorumlama yapar, geliştirici (sen) tüm konfigürasyonu yönetir.
- **Kapsam**:
  - Yeni migration 027: `users` tablosu (id, username, password_hash bcrypt, role TEXT IN ('operator','developer'), created_at, last_login)
  - Yeni migration 027: `sessions` tablosu (id, user_id, token, expires_at, created_at, ip)
  - Login UI: `/login` sayfası — username + password, başarılıysa cookie set
  - Auth middleware: `Depends(require_session)` — session yoksa /login'e redirect
  - Role decorator: `Depends(require_developer)` ve `Depends(require_operator)` — yetersizse 403
  - 60+ route'a uygun decorator (mekanik iş, tablo bazlı):
    - Operator yetkili: alarm acknowledge, alarm etiketleme (Faz 3'te), maintenance mode (per-instance + global), push abonelik kendi cihazı
    - Developer yetkili: tag/asset/threshold/connection/settings/knowledge base CRUD, model retraining, audit log temizleme, tüm Operator yetkileri
  - "Geliştirici Ayarları" menü grubu: sadece Developer için görünür (model registry, audit log viewer, knowledge base düzenleme, system maintenance toggle)
  - Initial bootstrap: setup.sh `custos-developer` kullanıcı oluşturur, openssl ile rastgele şifre, `.env`'e yazar (chmod 0600), kullanıcıya görünür çıktı verir
- **Deliverable**:
  - 2 migration
  - `shared/auth.py` (yeni modül, password hash + session token)
  - `dashboard/auth_routes.py` (login/logout)
  - `dashboard/app.py` 60+ route'a decorator
  - `templates/pages/login.html`
  - `templates/components/nav.html` rol-conditional menü
  - 8-10 unit test (login success/fail, role enforcement, session expiry)
- **Test kriteri**:
  - Login olmadan `/dashboard/sensors` → /login redirect
  - Operator login + POST /dashboard/threshold/.../delete → 403
  - Developer login + aynı POST → 200
  - Pytest yeşil

---

### V11-102 — TLS Self-Signed + Caddy

- **Faz**: 1
- **Sıra**: 2 (V11-101 sonrası)
- **Süre**: 3-4 saat
- **Bağımlılık**: V11-101 (auth varken TLS değerli)
- **Gerekçe (K12)**: LAN trafiği şifresiz; Wi-Fi ortamında session token sniff edilebilir. Self-signed sertifika ile şifreleme sağlanır; browser uyarısı tek seferlik (TOFU).
- **Kapsam**:
  - `deploy/setup.sh` adımı [12/12]: caddy kurulumu (Ubuntu apt), `/etc/caddy/Caddyfile` üret
  - openssl ile self-signed cert üretimi: `/etc/custos/tls/{cert.pem,key.pem}` (CN = IT'nin atadığı statik IP, 10 yıl geçerli)
  - Caddy config: `:443 → reverse_proxy 127.0.0.1:8000` + `tls /etc/custos/tls/cert.pem /etc/custos/tls/key.pem`
  - HTTP 80 → HTTPS 443 redirect
  - systemd: caddy.service active + enable
  - Dashboard `__main__.py` lifespan: HTTP-only header güvenlik (`Secure` cookie flag, `Strict-Transport-Security` 1 yıl, `X-Content-Type-Options nosniff`)
  - README_PILOT.md: "İlk girişte browser uyarısı çıkacak — Gelişmiş > Devam et" notu
- **Deliverable**:
  - setup.sh güncellemesi (~40 satır)
  - `deploy/Caddyfile.template`
  - `scripts/generate_tls_cert.sh`
  - README_PILOT.md ek bölüm
- **Test kriteri**:
  - `curl -k https://<IP>/dashboard/` → 200
  - `curl http://<IP>/dashboard/` → 301 (HTTPS redirect)
  - Browser ilk girişte uyarı, kabul sonrası 🔒 ikonu

---

### V11-103 — Push Çoklu Alıcı + Settings UI

- **Faz**: 1
- **Sıra**: 3
- **Süre**: 1.5 iş günü (12 saat)
- **Bağımlılık**: V11-101 (Operator role push abonelik ekleyebilmesi için)
- **Gerekçe (K3, K10, S3)**: Şu an `push_subscriptions` cihaz başına anonim. Çoklu kişi senaryosunda kim hangi bildirim alacak belli değil. Settings UI gerekli.
- **Kapsam**:
  - Migration 028: `push_subscriptions`'a kolon ekle:
    - `label TEXT NOT NULL DEFAULT ''` (örn. "Ali — Telefon")
    - `enabled BOOLEAN NOT NULL DEFAULT TRUE`
    - `notify_info BOOLEAN NOT NULL DEFAULT FALSE`
    - `notify_emergency BOOLEAN NOT NULL DEFAULT TRUE`
    - `created_by_user_id INTEGER REFERENCES users(id)`
  - `push_sender.py` `_should_notify` 4-tier filtre (info/warn/crit/emergency)
  - Settings sayfasında "Bildirim Alıcıları" yeni bölüm:
    - Kayıtlı abonelik listesi (label, role, severity tier'lar, sessiz saat, master toggle)
    - "Yeni cihaz ekle" butonu — Operator kendi cihazını ekleyebilir, label girer
    - Test bildirimi gönder butonu (her abonelik için)
    - Master switch: "Tüm push'lar geçici sustur" (örn. tatil)
  - Dashboard footer: "🔔 X aktif bildirim alıcısı" rozeti (Developer için)
- **Deliverable**:
  - Migration 028
  - `dashboard/app.py` push abonelik route'ları (CRUD + test)
  - `templates/pages/settings.html` Bildirim Alıcıları bölümü
  - `static/js/push.js` registration flow güncellemesi (label prompt)
  - 6-8 dashboard test (subscribe + unsubscribe + edit + role enforcement)
- **Test kriteri**:
  - 3 ayrı cihazdan abonelik ekle → 3 satır
  - Birinin enabled=false → push'tan o cihaz çıkar
  - Master switch off → hiç push gitmez
  - Operator başkasının aboneliğini silemez (404 veya 403)

---

### V11-104 — Test/Bakım Modu (Per-Instance + Global)

- **Faz**: 1
- **Sıra**: 4
- **Süre**: 1 iş günü (8 saat)
- **Bağımlılık**: V11-101 (her iki rol bakıma alabiliyor)
- **Gerekçe (K2, K15)**: Bakım sırasında alarm spam'i + eğitim setine kirli veri girişi. Per-instance + global kapsamlı toggle.
- **Kapsam**:
  - Migration 029:
    ```sql
    ALTER TABLE asset_instances ADD COLUMN
        maintenance_mode_until TIMESTAMPTZ DEFAULT NULL,
        maintenance_reason TEXT DEFAULT '',
        maintenance_started_by_user_id INTEGER REFERENCES users(id);

    ALTER TABLE alarm_events ADD COLUMN
        is_test BOOLEAN NOT NULL DEFAULT FALSE;

    -- Global mode (singleton retention_config'e):
    ALTER TABLE retention_config ADD COLUMN
        global_maintenance_until TIMESTAMPTZ DEFAULT NULL,
        global_maintenance_reason TEXT DEFAULT '',
        global_maintenance_started_by_user_id INTEGER;
    ```
  - `threshold_engine.py` _evaluate_threshold: alarm yazmadan önce check:
    - Global maintenance aktif ise `is_test=true`, push gitmez
    - Tag'in bağlı olduğu instance'ın `maintenance_mode_until > now()` ise `is_test=true`, push gitmez
  - Asset instance kartında "Bakım Modu" butonu + dropdown (1h/4h/12h/24h/3g/manuel)
  - Settings sayfasında "Sistem Modu" bölümü (global toggle + sebep + süre)
  - Background task (analytics loop): her 1 dk maintenance_mode_until expire kontrolü → otomatik kapat
  - Dashboard banner: global maintenance aktifken üstte sarı şerit "🔧 SİSTEM BAKIM MODU — XX:XX'da kapanır"
  - Audit log entry her toggle'da
- **Deliverable**:
  - Migration 029
  - `analytics/maintenance_mode.py` (yeni modül — expire checker)
  - threshold_engine güncelleme
  - Dashboard route + template güncellemesi
  - 6 unit + 4 integration test
- **Test kriteri**:
  - Per-instance bakım → o instance alarm üretmez, diğerleri üretir
  - Global bakım → hiç push gitmez, alarm DB'ye is_test=true yazılır
  - Süre dolunca otomatik normal mode (1 dk içinde)
  - Audit log'da kayıt var

---

### V11-105 — Watchdog (3 Katmanlı İç)

- **Faz**: 1
- **Sıra**: bağımsız (paralel)
- **Süre**: 5-6 saat
- **Bağımlılık**: yok
- **Gerekçe (K13)**: Servis çökerse müşteri 24 saat fark etmeyebilir. Pilot kabul kriteri "uptime ≥%99" gereği.
- **Kapsam**:
  - Migration 030: `service_heartbeats` tablosu (service_name, last_heartbeat TIMESTAMPTZ, status TEXT)
  - `custos.service` ve `custos-critical.service` unit dosyalarına `WatchdogSec=60` ekle
  - `__main__.py` lifespan: async task her 30 sn `sd_notify(WATCHDOG=1)` (python-systemd binding ya da sd-notify lib)
  - Critical loop her 60 sn DB'ye heartbeat yaz
  - Analytics loop her 120 sn cross-check: critical heartbeat > 180 sn yoksa "Critical loop kayıp" alarm üret (severity=crit)
  - Dashboard overview widget: "Sistem Sağlığı" rozeti (HTMX every 30s)
    - 🟢 yeşil: tüm servisler 60 sn içinde heartbeat
    - 🟡 sarı: bir servis 60-180 sn yanıtsız
    - 🔴 kırmızı: servis > 180 sn yanıtsız
  - `healthcheck.py`'a heartbeat freshness kontrolü ekle
- **Deliverable**:
  - Migration 030
  - `analytics/heartbeat.py` (yeni modül)
  - 2 systemd unit dosyası güncellemesi
  - Dashboard widget + template
  - healthcheck.py +1 kontrol
  - 4 unit + 2 integration test
- **Test kriteri**:
  - Critical loop manuel kill → 3 dk içinde "Critical loop kayıp" alarmı + dashboard kırmızı
  - systemd Critical'i restart et → yeşil dön
  - Healthcheck script heartbeat eski → exit 1

---

### V11-106 — DB User Ayrımı (custos_app + custos_admin)

- **Faz**: 1
- **Sıra**: bağımsız
- **Süre**: 3 saat
- **Bağımlılık**: yok
- **Gerekçe (K14)**: Tek `custos` user her şey yapar (DDL dahil). Runtime credential sızarsa migration + drop hakkı dahil. Ayrım blast radius'u küçültür.
- **Kapsam**:
  - `setup.sh` PG cluster bölümünü güncelle:
    ```sql
    CREATE USER custos_admin WITH PASSWORD '${ADMIN_PW}';
    CREATE USER custos_app   WITH PASSWORD '${APP_PW}';
    GRANT ALL PRIVILEGES ON DATABASE custos TO custos_admin;
    GRANT CONNECT ON DATABASE custos TO custos_app;
    -- Migration sonrası (alembic upgrade head sonrası ek adım):
    GRANT USAGE ON SCHEMA public TO custos_app;
    GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO custos_app;
    GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO custos_app;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO custos_app;
    ```
  - `.env` 2 ayrı DSN:
    - `CUSTOS_DB_DSN=postgresql://custos_app:...`  (runtime)
    - `CUSTOS_DB_ADMIN_DSN=postgresql://custos_admin:...` (alembic)
  - `alembic/env.py`: `CUSTOS_DB_ADMIN_DSN` öncelikli, yoksa `CUSTOS_DB_DSN` fallback
  - `pyproject.toml [project.scripts]`: `custos-migrate = "custos.cli:migrate"` — alembic upgrade wrapper'ı admin DSN ile koşar
- **Deliverable**:
  - setup.sh güncellemesi (PG cluster bölümü)
  - alembic/env.py güncellemesi
  - `.env.example` 2 DSN
  - README_PILOT.md geri yükleme prosedürü güncellemesi
- **Test kriteri**:
  - custos_app ile `DROP TABLE tag_readings` denemesi → permission denied
  - custos_admin ile aynı → başarılı (rollback ile geri al)
  - alembic upgrade head admin DSN ile çalışır

---

### V11-107 — Severity 4-Tier (info/warn/crit/emergency)

- **Faz**: 1
- **Sıra**: V11-103 öncesi (push severity filtresi 4-tier'a göre olmalı)
- **Süre**: 4 saat
- **Bağımlılık**: yok
- **Gerekçe (K10)**: Şu an severity TEXT, default 'warn'. UI/push policy net ayrım yok. ISA-18.2 4 katman.
- **Kapsam**:
  - Migration 031:
    ```sql
    -- Threshold severity'i CHECK constraint'e bağla
    ALTER TABLE thresholds ADD CONSTRAINT severity_enum
        CHECK (severity IN ('info', 'warn', 'crit', 'emergency'));
    -- Mevcut 'warn' default kalır
    ```
  - Threshold form'unda severity dropdown 4 seçenek + açıklama tooltipleri
  - Threshold form'unda "Self-test" onay kutusu — emergency seçilirse zorunlu görünür ("Bu gerçekten emergency mi? Yanlış emergency operasyon hayatını sıkıntıya sokar.")
  - threshold_engine emergency davranışı:
    - hysteresis bypass: `_can_clear_with_hysteresis` emergency için False döndürür (manuel acknowledge zorunlu)
    - debounce 1 sn'ye düşür (debounce_seconds field'ı var, emergency için runtime override)
    - alarm yazılırken `severity='emergency'` audit log
  - push_sender filtresi emergency: sessiz saat dahi bypass, "yüksek öncelik" notification flag'i
  - Dashboard badge renkleri:
    - 🔵 info: bg-blue
    - 🟡 warn: bg-yellow
    - 🟠 crit: bg-orange
    - 🔴 emergency: bg-red + animate-pulse
  - Alarm sayfasında severity filtre dropdown (info/warn/crit/emergency/hepsi)
- **Deliverable**:
  - Migration 031
  - threshold_engine güncelleme
  - push_sender güncelleme
  - Threshold form template güncelleme
  - Alarm sayfa template güncelleme
  - 6 test
- **Test kriteri**:
  - Threshold create severity=emergency → onay kutusu zorunlu
  - Emergency alarm → hysteresis bypass, manuel acknowledge zorunlu
  - Sessiz saatte emergency → push gider, warn gitmez

---

### V11-108 — Stuck-at Detection Layer 1 (Kural)

- **Faz**: 1
- **Sıra**: bağımsız
- **Süre**: 1.5-2 iş günü (12-16 saat)
- **Bağımlılık**: yok (Layer 3 ML personalize V11-302'de)
- **Gerekçe (K11)**: Sensör donması (kablo kopma, transmitter arıza) yakalanmıyor. Sıcaklık prob donar → false sağlıklı görünür. Pilot için hardcoded kurallar yeterli.
- **Kapsam**:
  - Migration 032:
    ```sql
    ALTER TABLE tags ADD COLUMN
        stuck_at_preset TEXT NOT NULL DEFAULT 'auto',  -- auto|none|fast|slow|very_slow|counter
        stuck_at_seconds INTEGER DEFAULT NULL;          -- override (NULL = preset)
    ```
  - Tag tipine göre default lookup tablosu (`shared/stuck_at_presets.py`):
    | Unit pattern | Preset | Saniye |
    |---|---|---|
    | `°C` (oda) | slow | 1800 |
    | `°C` (proses, su) | fast | 300 |
    | `bar`, `kPa` | fast | 300 |
    | `m³/h`, `L/s` | fast | 300 |
    | `Hz` | slow | 900 |
    | `%` (damper) | slow | 900 |
    | `kWh`, `m³` (counter) | counter | özel |
    | bool, set point | none | — |
  - Yeni modül `analytics/liveness_engine.py`:
    - 30 sn tick
    - Her aktif tag için `query_tag_readings(tag_id, now-2h, now)` (sadece son 2 saat)
    - Last value vs first value karşılaştır, last_change_seconds hesapla
    - Counter mode: değer azalıyorsa veya N saniye artmıyorsa alarm
    - Threshold aşılırsa `alarm_events`'e severity=warn alarm yaz
  - Tag form'unda "Sensör Sağlık" bölümü: preset dropdown + seconds override
  - Alarm sayfasında "Tip" sütunu: threshold | stuck-at | anomaly | sensor_health
- **Deliverable**:
  - Migration 032
  - `shared/stuck_at_presets.py`
  - `analytics/liveness_engine.py`
  - Tag form template güncelleme
  - 8 unit + 4 integration test (sıcaklık, basınç, akış, counter senaryoları)
- **Test kriteri**:
  - Mock 5 dk sabit basınç değeri → stuck-at alarm
  - Counter tag azalıyor → alarm
  - Set point tag (preset=none) → asla alarm yok
  - Auto-resolve: değer değişince alarm hysteresis ile kapanır

---

### V11-109 — Backup Hibrit C (Haftalık pg_dump + Günlük JSON)

- **Faz**: 1
- **Sıra**: bağımsız
- **Süre**: 4-5 saat
- **Bağımlılık**: yok
- **Gerekçe (K4)**: Felaket kurtarma + konfigürasyon değişiklik takibi. Tag readings ham veri Parquet'te zaten var; pg_dump konfigürasyonu da kurtarır.
- **Kapsam**:
  - `scripts/backup_pg_dump.sh`: Pazar 03:00 cron, `pg_dump -h localhost custos | gzip > /var/custos/backup/pg/custos-YYYYMMDD.sql.gz`, 30 gün retention (`find -mtime +30 -delete`)
  - `scripts/backup_config_json.py`: her gece 04:00, JSON dump:
    - `tags`, `connection_profiles`, `asset_instances`, `tag_bindings`, `thresholds`, `push_subscriptions`, `maintenance_*`, `retention_config`, `users` (password_hash dahil)
    - Çıktı: `/var/custos/backup/config/config-YYYYMMDD.json` (chmod 0600), 365 gün retention
  - `scripts/restore_config_json.py`: dosyadan yükleme — Developer manuel kullanır, dry-run flag'i (`--dry-run` farklı satırları gösterir)
  - `scripts/restore_pg_dump.sh`: README'de elle dokümante (pg_restore ile)
  - `setup.sh` cron entries:
    ```
    0 3 * * 0 custos /opt/custos/scripts/backup_pg_dump.sh
    0 4 * * * custos /opt/custos/.venv/bin/python /opt/custos/scripts/backup_config_json.py
    ```
  - Dashboard Settings'te "Yedekleme" bölümü (Developer-only):
    - Son pg_dump tarihi + boyutu
    - Son config snapshot tarihi
    - "Şimdi yedek al" butonu (manuel)
    - Restore wizard (dry-run önce gösterir, kullanıcı onaylar)
  - Restore dry-run **pilot kurulum öncesi** test edilmeli
- **Deliverable**:
  - 2 backup scripti
  - 1 restore scripti (config JSON)
  - setup.sh cron + dizin yapısı (`/var/custos/backup/{pg,config}`)
  - Dashboard Settings yeni bölüm
  - README_PILOT.md restore prosedürü genişletme
  - 4 unit test (config JSON dump/load round-trip)
- **Test kriteri**:
  - Tek tıklamayla pg_dump al → dosya oluşur
  - Config JSON 24 saat sonra önceki günden farklı (eğer threshold eklendiyse)
  - Restore dry-run staging DB'de — orijinal veriyle birebir uyum

---

### V11-110 — Knowledge Base Hibrit Yapı (Git + Local)

- **Faz**: 1
- **Sıra**: V11-000-F öncesi (içerik bunun üstüne dolar)
- **Süre**: 4 saat
- **Bağımlılık**: V11-101 (Developer-only knowledge düzenleme)
- **Gerekçe (K6)**: Temel dokümanlar git'te versiyon kontrolünde, müşteri-spesifik veya saha tecrübeleri lokal düzenlenebilir.
- **Kapsam**:
  - `analytics/assistant/loader.py` iki dizini birleştirir:
    - `data/knowledge/` (Git, repo içinde) — temel içerik
    - `/var/custos/knowledge/local/` (lokal, gitignore) — saha-spesifik
  - Local doküman aynı slug'a sahipse override eder (lokal git'i yener)
  - Settings sayfasında "Knowledge Base" bölümü (Developer-only):
    - Git dokümanları listesi (read-only, "düzenlemek için git pull/edit/push")
    - Local dokümanlar listesi (CRUD)
    - "Yeni lokal doküman ekle" butonu — Markdown editor (HTMX)
    - "İndeksi yeniden oluştur" butonu (FAISS reload)
  - setup.sh: `/var/custos/knowledge/local/` dizini oluştur (chown custos:custos, chmod 0750)
- **Deliverable**:
  - loader.py güncelleme (multi-source)
  - Dashboard knowledge sayfası
  - setup.sh güncelleme
  - 4 unit test (override semantics, hot reload)
- **Test kriteri**:
  - Aynı slug git + local → local döner
  - Local doküman ekle → asistan 30 sn içinde indeksler
  - Operator login → knowledge sayfası 403

---

### V11-111 — Resource Alarm (CPU / RAM > 90%)

- **Faz**: 1
- **Sıra**: V11-105 sonrası
- **Süre**: 3 saat
- **Bağımlılık**: V11-105 (heartbeat + alarm pattern)
- **Gerekçe**: endurance_metrics CSV'ye yazıyor ama alarm üretmiyor. CPU veya RAM 5 dk üst üste %90+ → push uyarı.
- **Kapsam**:
  - `analytics/disk_telemetry.py` pattern'ini genişlet (`resource_telemetry.py` veya aynı modüle ekle)
  - 5 dk pencere ortalaması: `psutil.cpu_percent(interval=1)` 60 sn'de bir → son 5 değer ortalama
  - Eşik aşımı 5 dk üst üste → audit log + push (severity=warn, "CPU yüksek" / "RAM yüksek")
  - 6 saat in-memory cooldown (disk_telemetry pattern'iyle aynı)
  - Settings'te eşik override (default %90, custom 70-95 arası)
- **Deliverable**:
  - resource_telemetry.py modülü
  - lifespan'a resource monitor task eklenmesi
  - Settings UI eşik input
  - 4 unit test
- **Test kriteri**:
  - Stress test (`stress-ng --cpu 4 --timeout 360`) → 5 dk sonra alarm
  - Cooldown çalışıyor (tekrar tetiklenmez)

---

### V11-112 — OS Sertleştirme (PG localhost-only + SSH disable + LUKS runbook)

- **Faz**: 1
- **Sıra**: bağımsız
- **Süre**: 2 saat
- **Bağımlılık**: yok
- **Gerekçe**: Şu an PG default ile localhost dinleyebilir (kontrol edilmemiş). SSH password-auth açıksa brute-force riski. LUKS disk şifreleme OS-level adım.
- **Kapsam**:
  - `setup.sh` adımı [N/12] — PG hardening:
    ```bash
    # listen_addresses doğrulama
    sudo -u postgres psql -c "ALTER SYSTEM SET listen_addresses TO 'localhost';"
    sudo systemctl restart postgresql

    # pg_hba.conf — sadece localhost host based auth
    PG_HBA="/etc/postgresql/16/main/pg_hba.conf"
    if ! grep -q "^host.*custos.*127.0.0.1/32.*md5" "$PG_HBA"; then
        echo "host    custos    custos_app    127.0.0.1/32    md5" >> "$PG_HBA"
        echo "host    custos    custos_admin  127.0.0.1/32    md5" >> "$PG_HBA"
    fi
    ```
  - `setup.sh` SSH hardening:
    ```bash
    sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
    sed -i 's/^#*PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
    systemctl restart sshd
    ```
  - LUKS runbook bölümü `README_PILOT.md`:
    - Ubuntu 24.04 installer "Encrypt the new Ubuntu installation" seçeneği
    - Boot şifre kararı (anahtar müşteride mi geliştiricide mi — k6 paralel)
    - Recovery key yedekleme (dışarıda kasada)
- **Deliverable**:
  - setup.sh +30 satır
  - README_PILOT.md LUKS bölümü
- **Test kriteri**:
  - `psql -h <external_ip>` → connection refused
  - `ssh -o PreferredAuthentications=password` → "Permission denied"

---

### V11-113 — NTP Doğrulama + Healthcheck Genişletme

- **Faz**: 1
- **Sıra**: bağımsız
- **Süre**: 2 saat
- **Bağımlılık**: yok
- **Gerekçe**: Saat sapması → alarm timestamp yanlış, trend bozuk. systemd-timesyncd default Ubuntu'da gelir ama doğrulanmamış.
- **Kapsam**:
  - `setup.sh`: `timedatectl set-ntp true` + check
  - `healthcheck.py`'a +1 kontrol: `ntp_synced` (timedatectl status output parse)
  - Eşik: drift > 2 sn → fail
  - README_PILOT.md NTP server konfigürasyonu (müşteri internal NTP varsa)
- **Deliverable**:
  - setup.sh +5 satır
  - healthcheck.py +1 metod
  - README_PILOT.md ek
- **Test kriteri**:
  - `healthcheck.py --json` çıktısında `ntp_synced: true`
  - Manuel saat 5 sn ileri al → check fail

---

## 4. Faz 2 — Pilot Süreci (5 Haz – 5 Tem 2026)

Saha + hata düzeltme + eğitim. Yeni feature **DEĞİL**, sadece operasyonel.

### V11-200 — Pilot Kurulum (5-6 Haziran)

- IT statik IP atadıktan sonra setup.sh dry-run + canlı kurulum
- Modbus map doğrulama (Regin PLC, 200 tag civarı)
- Operator hesabı oluşturma (müşteri yetkilisi)
- Push abonelik kayıtları (3-5 cihaz)
- 24 saat gözetim (geliştirici saha)

### V11-201 — Saha Eğitimi (1. hafta)

- Operatör 1 saatlik dashboard tur
- Bakım modu kullanımı + 4 sınıf etiketleme demo
- Push bildirim testi
- Knowledge base kullanımı (asistan)

### V11-202 — Hot-Fix Penceresi (Hafta 1-2)

- Saha-spesifik bug'lar: stuck-at preset ayarı, threshold tuning, push timing
- Memory hızlı güncelleme + commit + push
- Acil çıkış: rollback prosedürü (V11-109 restore)

### V11-203 — Stabilizasyon (Hafta 3-4)

- Anomaly detector ilk eğitimi (en az 1 hafta veri biriktikten sonra)
- KPI baseline raporu
- Müşteri haftalık check-in
- Knowledge base saha-spesifik genişletme (Operator alarm yorumları + bakım notları → V11-110 lokal docs)

### V11-204 — Pilot Kabul Testi (5 Tem)

- Brief v1.7 §2.1 başarı kriterleri:
  1. Uptime ≥ %99 (V11-105 watchdog log'larında doğrulanır)
  2. Operator dashboard'u günlük kullanmış
  3. ≥1 gerçek alarm (eşik veya anomali)
  4. ≥1 asset instance binding tamamlanmış
  5. Asistan ≥1 doğru cevap (V11-000-F içerik)
  6. ≥1 bakım checklist alarm sonrası kullanılmış
  7. 30 günlük chart sorgusu < 200 ms
  8. Parquet arşivi otomatik üretilmiş
  9. Yazılı pozitif geri bildirim
- **Çıktı**: Pilot kabul raporu + Faz 3 sprint planı

---

## 5. Faz 3 — Pilot Sonrası Olgunlaşma (Tem 2026 – Ocak 2027)

3-6 ay vade. Memory v1.0.1 backlog 8 kalemi (Faz 0) + yeni özellikler.

### V11-301 — 4 Sınıf Alarm Etiketleme + Review Queue

- **Süre**: 2-3 iş günü
- **Bağımlılık**: V11-101 (Operator role), V11-104 (is_test flag)
- **Gerekçe (K16)**: Operatör "Gerçek / Yanlış / Bakım / Bilinmiyor" etiketi koyabilmeli; eğitim seti temizliği için kritik.
- **Kapsam**:
  - Migration 033: `alarm_labels` tablosu (alarm_event_id, label, user_id, comment, created_at)
  - Alarm sayfasında "Acknowledge" formuna 4 radio + comment textarea
  - "Bilinmiyor" 30 gün etiketsiz kalırsa otomatik dışlanır
  - Review queue: "Bilinmiyor" + "Bilinmiyor zamanlanmış" listesi (Developer + Operator)
  - Stats sayfası: precision, FP rate, etiket dağılımı (Developer-only)
  - anomaly_detector eğitiminde `WHERE label IN ('false_positive','maintenance') OR label IS NULL` ile temizleme
- **Deliverable**:
  - Migration 033
  - Alarm acknowledge UI güncelleme
  - Review queue sayfası
  - Stats sayfası
  - anomaly_detector eğitim filtresi
  - 8 test
- **Test kriteri**:
  - Etiket konunca audit log'a yazılır
  - Bakım etiketli alarmlar yeniden eğitimde dışlanır
  - Stats: precision = (gerçek arıza) / (toplam etiketli)

### V11-302 — Stuck-at Layer 3 ML Personalize

- **Süre**: 3-4 iş günü
- **Bağımlılık**: V11-108 (Layer 1 kural), 2 hafta veri
- **Gerekçe (K11)**: Per-tag baseline öğrenir, hardcoded eşiği dinamik adapte eder.
- **Kapsam**:
  - Yeni modül `analytics/liveness_ml.py`:
    - Her tag için son 14 gün'lük "değişim aralıkları" topla (saniye listesi)
    - Median + 99 percentile hesapla
    - Personalize threshold = max(hardcoded_preset, p99 × 2)
    - Yetersiz veri (< 100 sample) → hardcoded'a fallback
  - liveness_engine'i hibrit moda al: önce ML, fallback hardcoded
  - Settings'te "Sensör Sağlık ML" toggle (default: kapalı, açmak isteyen açar)
- **Deliverable**: ML modülü + entegrasyon + 4 test

### V11-303 — Shadow Mode + Model Registry + Otomatik Retraining

- **Süre**: 3 iş günü
- **Bağımlılık**: V11-301 (etiket veriyle iyileşme)
- **Gerekçe (K9)**: Yeni modeller otomatik shadow'a düşer, sen onaylarsın.
- **Kapsam**:
  - Migration 034: `model_registry` tablosu (instance_id, version, file_path, mode, trained_at, training_rows, contamination, lookback_hours, notes)
  - anomaly_detector refactor: "iki model birden çalıştır" (live + shadow)
  - Shadow alarm'lar `audit_log`'a `category='shadow_anomaly'` (operatör görmez)
  - Otomatik retraining: her Pazar 03:00, `training_data_filter` (V11-301 etiket dışlama)
  - Settings'te "Model Versiyonları" sayfası (Developer-only):
    - Live + Shadow karşılaştırma (alarm sayısı, FP tahmini)
    - "Live'a yükselt" butonu
    - "Sil" butonu
    - 30 gün otomatik archive cleanup cron
- **Deliverable**: Migration + anomaly_detector güncelleme + UI + cron + 6 test

### V11-304 — Rate-of-Change + Range Sanity (Layer 1 ek)

- **Süre**: 1.5 iş günü
- **Gerekçe**: Ani sıçrama (kablo arızası, EMI) yakalanmıyor; sensör range dışı yazıyorsa farkedilmiyor.
- **Kapsam**:
  - Tag tablosuna `rate_of_change_max` ve `range_min`/`range_max` kolonları
  - liveness_engine'e iki ek check
  - Tag form güncelleme

### V11-305 — Cross-Sensor Consistency (KPI tabanlı)

- **Süre**: 2 iş günü
- **Gerekçe**: "Supply temp - return temp = ΔT, normal aralıkta mı" gibi tutarlılık kontrolü.
- **Kapsam**: KPI motoru zaten var; yeni KPI tipi `consistency_check` (formula = expression, severity = warn/crit). Sapma threshold'ı KPI sonucunda alarm'a çevrilir.

### V11-306 — Severity Escalation

- **Süre**: 1 iş günü
- **Gerekçe (K10)**: Critical alarm 5 dk yanıtsız → emergency'ye yükselir.
- **Kapsam**:
  - Threshold form'unda `escalation_minutes INTEGER DEFAULT NULL` (NULL = devre dışı)
  - threshold_engine'e escalation timer
  - audit log'da escalation event

### V11-307 — Mode-Aware (Operating Modes)

- **Süre**: 3-4 iş günü
- **Gerekçe**: Pump start/stop sırasında "anomali" false positive bombardımanı.
- **Kapsam**: Asset instance bazlı operating_mode (running/startup/shutdown). Manuel mode toggle (operatör), anomaly_detector mode-conditional model.

### V11-308 — EWMA / CUSUM / MAD-Score (Layer 2)

- **Süre**: 3-4 iş günü
- **Gerekçe**: Yavaş drift (compressor degradation) yakalama. scipy/statsmodels yeterli; PyOD'a gerek yok.
- **Kapsam**: Yeni `analytics/spc_engine.py` (Statistical Process Control). Tag bazlı EWMA + CUSUM + MAD-score. Sapma threshold'ı SPC alarm üretir.

### V11-309 — Faz 0 Kalemleri Birleştirme

Faz 0 V11-000-B, C, D, E, G, H bu fazda kapatılır (hepsi zaten "v1.1 ertelendi" işaretli).

---

## 6. Faz 4 — v1.2+ Uzun Vade (Vizyon)

Bu plan v1.1 kapsamı dışında, ileride değerlendirilecek.

- **Custos Benchmark**: Çapraz-tenant anonim aggregate (vizyon §3)
- **N-client Modbus pool**: Tek socket → N socket pool
- **BACnet/IP desteği**: AVM otomasyonunda yaygın
- **Cloud sync (opsiyonel müşteri kontrollü)**: Vizyon §2.4
- **Multi-site / multi-tenant**: AVM + fabrika tek dashboard
- **PWA + native mobile**
- **Chatbot çok turlu diyalog**
- **Bakım PDF raporu**
- **Enerji verimliliği raporu (ESG/CBAM)**
- **AR-GE raporu (yıllık AVG/MAX/STDDEV + olay korelasyonu)**

---

## 7. Bağımlılık Matrisi (Faz 1)

```
                                 V11-101 (auth)
                                    ├── V11-102 (TLS)
                                    ├── V11-103 (push UI)
                                    ├── V11-104 (bakım UI)
                                    └── V11-110 (knowledge base UI)
                                          └── V11-000-F (KB içerik)

  V11-105 (watchdog) ── V11-111 (resource alarm)

  V11-106 (DB user) — bağımsız
  V11-107 (severity 4-tier) — V11-103 öncesi (push severity filtre)
  V11-108 (stuck-at L1) — bağımsız
  V11-109 (backup hibrit) — bağımsız
  V11-112 (OS sertleştirme) — bağımsız
  V11-113 (NTP) — bağımsız
  V11-000-A (PAT rehber) — bağımsız (paralel dokümantasyon)
```

---

## 8. Sıralı Sprint Planı (Faz 1)

6 haftalık takvim, 1.5-2 hafta net efor, geniş buffer. Önerilen akış:

### Hafta 1 (27 Nis – 3 May): Auth + Watchdog Temeli

- **Pazartesi-Salı**: V11-101 (Auth + 2 rol) — 1 iş günü, en kritik
- **Çarşamba**: V11-105 (Watchdog) — 5-6 saat
- **Perşembe**: V11-107 (Severity 4-tier) — 4 saat
- **Cuma**: V11-106 (DB user ayrımı) — 3 saat
- **Buffer**: bug fix + push regresyon

### Hafta 2 (4 May – 10 May): Push + TLS + Bakım Modu

- **Pazartesi-Salı**: V11-103 (Push çoklu alıcı) — 1.5 iş günü
- **Çarşamba**: V11-102 (TLS + caddy) — 4 saat
- **Perşembe-Cuma**: V11-104 (Test/bakım modu) — 1 iş günü

### Hafta 3 (11 May – 17 May): Stuck-at + Backup

- **Pazartesi-Salı**: V11-108 (Stuck-at Layer 1) — 1.5-2 iş günü
- **Çarşamba**: V11-109 (Backup hibrit) — 4-5 saat
- **Perşembe**: V11-111 (Resource alarm) — 3 saat
- **Cuma**: V11-112 (OS sertleştirme) — 2 saat + V11-113 (NTP) — 2 saat

### Hafta 4 (18 May – 24 May): Knowledge Base + Faz 0 Dokümantasyon

- **Pazartesi**: V11-110 (KB hibrit yapı) — 4 saat
- **Salı-Cuma**: V11-000-F (KB içerik 20-30 doküman) — 3-7 iş günü, paralel başla
- **Buffer**: V11-000-A (PAT rehber) yarım gün

### Hafta 5 (25 May – 31 May): Saha Hazırlığı + Dry Run

- **Pazartesi-Salı**: Tam dry-run yeni Ubuntu 24.04 VM'de (`docs/pilot_denetim_plani_v1.md` A7)
- **Çarşamba**: Restore dry-run (V11-109)
- **Perşembe**: Knowledge base saha-eklentileri (Torunlar BMS dokümanı + ekipman listesi)
- **Cuma**: Pilot mini PC kurulum simülasyonu

### Hafta 6 (1 May – 4 Haz): Buffer + Kabul Hazırlığı

- Acil bug fix
- Pilot operatör eğitim materyali (1 sayfa cheat sheet)
- Pilot kurulum runbook tazeleme
- **5 Haziran**: Pilot kurulum

---

## 9. Risk ve Azaltma

| Risk | Olasılık | Etki | Azaltım |
|---|---|---|---|
| Auth migration mevcut kullanıma engel olur | Düşük | Yüksek | İlk login bootstrap setup.sh ile otomatik; staging'de migration test |
| Caddy SSL kurulumda PG/dashboard çakışması | Düşük | Orta | dry-run VM'de 1 hafta kalıcı test |
| Stuck-at kuralları AVM gerçekliğine uymaz (false positive bombardımanı) | Orta | Orta | Saha gün 1 sonrası eşik tuning, V11-302 ML hızlandırılabilir |
| Knowledge base 20-30 doküman yetişmez | Orta | Orta | İlk 10 doküman pilot kabul kriteri için yeter; gerisi pilot süresi içinde paralel |
| Operatör login UX kabul etmez | Düşük | Yüksek | TOFU pattern, "tek kez" söz; auto-logout 12 saat (LAN trust) |
| 6 hafta yetmez | Düşük | Yüksek | Hafta 5 pilot kurulum öncesi go/no-go check; gerekirse V11-110 saha içeriği pilot süresince |
| Ses ekosistemi çakışmalar (Restart=always vs WatchdogSec çakışma) | Düşük | Orta | systemd dökümantasyonu net; staging'de 24h test |

---

## 10. Tamamlanma Kriterleri (Faz 1)

Pilot kurulum gününden önce şu doğrulanmış olmalı:

- [ ] `pytest tests/` 0 fail
- [ ] `ruff check . && mypy src/` temiz
- [ ] `architecture_check.py` 11/11 yeşil
- [ ] Yeni Ubuntu 24.04 VM'de `setup.sh` 0 manuel müdahale
- [ ] Dashboard auth çalışıyor (operator + developer)
- [ ] Self-signed TLS browser TOFU aktif
- [ ] 4 systemd service (custos, custos-critical, caddy, postgresql) Restart politikalı
- [ ] Watchdog: critical loop kill → 3 dk içinde alarm
- [ ] Backup pg_dump + config JSON cron kurulu
- [ ] Restore dry-run staging başarılı
- [ ] Stuck-at en az 4 tag tipinde test
- [ ] 4-tier severity UI + push çoklu alıcı 3 cihazla test
- [ ] Per-instance + global bakım modu test
- [ ] Knowledge base 10+ doküman yüklü, asistan 5 örnek soruda doğru cevap
- [ ] OS sertleştirme: PG localhost-only, SSH password disable, NTP sync
- [ ] LUKS Ubuntu installer adımı runbook'ta net
- [ ] Pilot dry-run checklist (`_personal/pilot/deploy_dry_run_checklist.md`) ≤30 dk

---

## 11. Karar Onay Mührü

Bu plan 26 Nisan 2026 oturumunda Göktürk + Claude arasında kararlaştırılmıştır. K1-K16 kararları üzerine kuruludur. Değişiklik versiyon artırımı ile yapılır (sessiz düzenleme yasak — CLAUDE.md kural 9).

**Hazırlayan**: Claude (Opus 4.7, 1M context)
**Onaylayan**: Göktürk Ömer (solo kurucu)
**Sonraki güncelleme**: Pilot kabul testi (5 Tem 2026) sonrası Faz 3 detay revizyonu

---

**EOF**
