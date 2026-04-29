# Değişiklik Günlüğü

Bu projedeki tüm dikkate değer değişiklikler bu dosyada belgelenir.

Format [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) standardına dayanır,
sürümleme [Semantic Versioning](https://semver.org/spec/v2.0.0.html) kullanır.

Tarihler UTC. Tüm sürümler iç saha kullanımı için planlıdır (Custos lokal edge ürünü).

---

## [Yayınlanmamış]

Pilot öncesi denetim (29 Nis 2026) kapsamında yapılan iyileştirmeler.

### Eklendi
- **G-A güvenlik düzeltmeleri** — TLS sertifika doğrulama bypass kapatıldı, brute-force koruması (IP + kullanıcı adı bazlı login rate limit) sertleştirildi.
- **G-B test sertleştirme** — Mock kullanımı minimize edildi, kritik modüllerde coverage iyileştirildi, test hijyeni (`tests/load/` klasörü ayrıldı, izolasyon sorunları giderildi).
- **G-C dokümantasyon** — `CHANGELOG.md` (bu dosya) ve `SECURITY.md` (güvenlik açığı bildirim politikası) eklendi.

### Değiştirildi
- README test sayıları güncel: 792 test (361 unit + 229 integration + 187 dashboard + 15 load).

---

## [v1.1.0] — 2026-04-28 (planlı pilot sürümü)

İlk saha pilot sürümü. Hedef: Torunlar GYO, 5 Haziran 2026 kurulum.
v1.0 feature-complete üzerine pilot öncesi sertleştirme + olgunlaşma katmanı.

### Eklendi
- **Kimlik doğrulama (V11-101)** — Dashboard auth + iki rol (operator / developer).
- **Paket 02 — Operasyonel temeller**: Watchdog (heartbeat + auto-restart), DB user ayrımı (admin / app), severity 4-tier alarm modeli.
- **Paket 03 — Bildirim & şifreleme**: Push çoklu alıcı, TLS (Caddy reverse proxy + self-signed sertifika).
- **Paket 04 — Bakım modu**: Per-instance + global maintenance toggle, alarm bastırma.
- **Paket 05 — Liveness motoru**: Stuck-at + counter mode liveness tespiti.
- **Paket 06 — Saha sertleştirme**: Backup hibrit C (rsync + dump), resource alarm (CPU/RAM/disk eşik), OS sertleştirme, NTP zorunlu.
- **Paket 07 — Bilgi tabanı altyapısı**: KB hibrit yapı + PAT (Personal Access Token) rehber.
- **Paket 08 — Bilgi tabanı içerik**: 23 doküman + 48 Q&A + smoke test 10/10.
- **Paket R-01 — UI mikro rütuş**: Tipografi, ikon, ikincil aksiyon hizası.
- **Paket R-02 — Chart historik veri**: Uzun pencere + custom date range desteği.
- **Paket R-03 — Chart pan-fetch**: Sola kaydır → eski veri otomatik yüklen.
- **Paket R-04 — ML hub iskeleti**: Anomali model yönetim altyapısı.
- **Paket R-05 — Etiketleme + review queue (V11-301)**: Alarm etiketleme akışı.
- **Paket R-05a — Alarm sayfası N+1 → tek JOIN** performans iyileştirmesi.
- **Paket R-06 — Layer 1 ek kurallar (V11-304/305/306)**: Topology + drift tespit.
- **Paket R-07 — Mode-aware iskelet + SPC streaming (V11-307/308)**.
- **PP-01** — pip-audit ile bağımlılık CVE'leri 5 → 1 düşürüldü, `requirements.lock` üretildi.
- **PP-02** — GitHub Actions üzerinde haftalık pip-audit + Dependabot kuruldu.
- **PP-03** — `LICENSE` dosyası eklendi, README pilot Modbus VLAN notu yazıldı.
- **PP-06** — Login rate limit (IP + kullanıcı adı bazlı) + session GC task.
- **PP-07** — Güvenlik HTTP başlıkları: CSP + Permissions-Policy + COOP + CORP.
- **PP-08** — Kritik modüllerde unit test boost (+36 test, collector / threshold / db).
- **PP-09** — Integration test DB izolasyonu (`CUSTOS_TEST_DSN` ile ayrı veritabanı).

### Değiştirildi
- bcrypt 4.3.0 → 5.0.0 lockfile sync.
- Dependabot otomatik bağımlılık güncellemeleri (faiss-cpu, bcrypt, pyyaml).

### Düzeltildi
- **v1.0.1 tech debt** (23 Nis 2026 — endurance WSL dry-run bulguları): `setup.sh` sıfır müdahale rewrite, `endurance_setup.sh` exec bit + sıfır müdahale, alembic env.py asyncpg-native, fast polling budget default 10 → 60, metrics daemon systemd unit + PID dosyası, daily_check disk WARN şartı, `.gitattributes` shell script exec bit, UI/UX bug fix'leri (5 kalem), `psycopg2-binary` fallback.
- Dashboard nav push-count fetch path prefix eksiği.

### Kaldırıldı
- `config/sensors.toml` ölü artefakt (PP-04).

---

## [v1.0.0] — 2026-04-22 (feature-complete)

İlk feature-complete milestone. Tüm çekirdek özellikler tamamlandı, pilot
hazırlığı için temel sürüm.

### Eklendi

#### Çekirdek altyapı
- **Aşama 1 — İskelet**: Proje yapısı, araçlar (ruff, mypy strict, pytest), `CLAUDE.md` kuralları.
- **Aşama 2 — Veri katmanı**: Docker Compose + TimescaleDB + Alembic migration sistemi + abstract DB arayüzü (`shared/database.py`).
- **Aşama 3 — Walking skeleton**: Modbus simulator + Collector + uçtan uca gerçek veri akışı.

#### Critical loop (Modbus collector)
- **F11 Paket I — Batch Modbus Read**: Register gruplama algoritması, register type decoder (uint16/int16/uint32/int32/float32), fallback senaryosu.
- **F11 Paket G — Collector paralelleştirme** + budget enforcement.

#### Analytics loop
- **F5 — Threshold Engine**: ISA-18.2 uyumlu alarm state machine (debounce + hysteresis), Alarm sayfası, Audit Logs.
- **F6 — KPI Motoru**: AST-tabanlı formül engine, KPI Dashboard, ML Anomali Tespiti (Isolation Forest).
- **F7 — Web Push Bildirim**: VAPID anahtarları, severity filtresi, sessiz saat, Settings sayfası.
- **F8a — Bakım modülü**: DB layer + period math + MaintenanceScheduler + UI + alarm entegrasyonu + Overview widget.
- **F8b — Teknik Asistan Chatbot**: KB tabanlı yardımcı.

#### Dashboard (F1-F4)
- **F1**: Dashboard shell, tasarım dili, component kütüphanesi.
- **F2**: Tag modeli, CRUD, Sensors sayfası.
- **F3**: Modbus Auto-Scan, Connection Profiles, Per-tag Polling (Slow 10s / Normal 1s / Fast 100ms), canlı değer.
- **F4**: Asset Template Library, Binding, Processes sayfası.

#### Saha şablonları (F9 — AVM Template Pack)
- **HVAC**: Chiller, AHU, FCU, Cooling Tower.
- **Pompa & basınç**: Booster, Sirkülasyon, Terfi A/B.
- **Enerji**: Enerji Analizörü.
- Dashboard içinde "AVM Template Pack" sekmesi + seed API + integration testler.

#### Pilot deploy (F10)
- `setup.sh` finalize: TimescaleDB zorunlu + sistem kontrol + openssl şifre + seed + exit code'lar.
- `healthcheck.py` 6 kontrollü + JSON çıktı + VAPID `--write-env` + public_bytes fix.
- systemd service ikilisi: `custos.service` (dashboard) + `custos-critical.service` (collector).
- `deploy/README_PILOT.md` 15 bölüm saha rehberi (brief v1.7 spec).

#### Historian & Retention (F11 Paket A-H)
- **Paket A**: TimescaleDB production hardening (chunk + compression + retention).
- **Paket B**: Continuous aggregates (1min + 1hour).
- **Paket C**: Auto-resolution query API.
- **Paket D**: Dashboard auto-resolution + gather paralelleştirme.
- **Paket E**: Parquet aylık arşiv job (3 yıl saklama).
- **Paket F**: Retention UI + disk telemetri.
- **Paket H**: Query guard (uzun query iptali).

#### Operasyon araçları
- **Bulk Import Helper**: HTMX modal + CSV/YAML pydantic schema + parser + commit logic + örnek dosya sunumu.
- **Endurance Framework**: 5 AVM instance + role-tag binding scripti + 200 tag CSV generator + 5 dakikalık metrics daemon + günlük rapor + tek komutluk WSL kurulum scripti.
- **Overview chart sistemi**: Dinamik slot + multi-axis + per-tag mode + decimation (TimescaleDB time_bucket AVG, ~600 nokta) + compact mode.

#### Mimari & güvenlik denetimi
- **A1**: Mimari kural check scripti (11 kural, pre-commit + CI).
- **A2**: Test coverage baseline (%66 genel, collector %93 / threshold %75 / db %89, `fail_under=65`).
- **A3**: pip-audit taraması (4 bulgu, 2 HIGH kapatıldı: lxml + setuptools).
- GitHub Actions Node.js 24 geçişi (2 Haz 2026 deprecation öncesi).

### Değiştirildi
- pymodbus sürümü `<3.13.0`'e pinlendi (3.13 SimData API geçişi v1.1'e ertelendi).

### Düzeltildi
- Scanner testlerinde port çakışması (TIME_WAIT).
- Buffer polish: timezone fix, gerçek overview grafikleri, chart tag seçimi.

### Kaldırıldı
- Ölü `fake_data.py` artefaktı.
- Kullanılmayan `sensor_config.py` (kod denetim temizliği).
