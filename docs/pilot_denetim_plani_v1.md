# Custos — Pilot Öncesi Kod ve Sistem Denetim Planı v1

**Oluşturulma:** 22 Nisan 2026
**Pilot Go-Live:** 5 Haziran 2026 (**44 gün**)
**Amaç:** Sahaya çıkmadan önce kod kalitesi, mimari uyum ve sistem dayanıklılığını ölç; gizli kalmış riskleri pilot öncesi bul.
**Kaynak belgeler:** [CLAUDE.md](../CLAUDE.md), [custos_is_plani_v1.md](custos_is_plani_v1.md), [brief_v1.7.md](brief_v1.7.md)

---

## Genel bakış

Custos "vibe coding" akışıyla hızlı geliştirildi. Mevcut altyapı sağlam (ruff, mypy strict, pytest-cov, pre-commit, 129 test) ama bu sadece "kod çalışır mı"yı garanti eder. Bu plan şunu garanti eder: **kod CLAUDE.md'deki kurallara gerçekten uyuyor + sistem 14 gün kesintisiz çalışıyor + deploy temiz bir makinede sorunsuz**.

Pilot kabul kriteri "14 gün kesintisiz çalışma" — bu testi sahada ilk kez görmek istemeyiz. **Şimdi kontrollü ortamda doğrulanacak.**

### Öncelik matrisi

| # | Adım | Süre | Ne zaman | Kritik mi? |
|---|---|---|---|---|
| 1 | Mimari kural denetim scripti | 2–3 saat | W2 ✅ TAMAM | **Evet** |
| 2 | Test coverage ölçümü | 2 saat | W2 ✅ TAMAM (baseline %66.50, fail_under=65) | **Evet** |
| 3 | Dependency zafiyet taraması | 30 dk | W2 ✅ TAMAM (2 HIGH açık — fix kararı bekliyor) | Evet |
| 4 | Bağımsız kod incelemesi (subagent) | 4–6 saat | **W6** (feature-complete sonrası) | Evet |
| 5 | Uzun süreli dayanıklılık testi | **Pasif 7 gün** | **W7 başı (28 May - 3 Haz)** — feature freeze sonrası | **Evet** |
| 6 | Chaos/recovery testleri | 1 gün | W6 | Evet |
| 7 | Deploy dry-run (temiz WSL2 instance) | 1 gün | W7 başı | **Evet** |
| + | Coverage boost (A4 bulgularına göre) | 1–2 gün | W7 (A4 sonrası) | Opsiyonel — regression guard `fail_under=65` zaten var |

**Revize akış gerekçesi (22 Nisan 2026):** "Önce fix/borç → sonra test" prensibi. F9 + F10 + Paket I + saha entegrasyonu feature-complete olmadan A4/A5/A6/A7 yapmak eksik denetim demektir. Feature freeze 27 Mayıs akşam, ardından denetim yoğunluğu W6-W7'de.

---

## Adım 1 — Mimari kural denetim scripti

### Amaç
CLAUDE.md'deki 10 mimari kural **şu an insana güveniyor**. Makine ile doğrula. Her kural ihlali = kırmızı = commit engellenir.

### Süre
2–3 saat (bugün/yarın).

### Önkoşul
- `.venv` aktif
- `ruff` çalışıyor
- `grep`/`ripgrep` kullanılabilir

### Adımlar

- [ ] **1.1** — Yeni dosya oluştur: `scripts/architecture_check.py`. Script, her kuralı ayrı bir fonksiyon olarak yazsın; ihlal bulursa satır no + dosya yolu ile `stderr`'a yazıp `sys.exit(1)` yapsın.

- [ ] **1.2** — Script aşağıdaki **10 kuralı** kontrol etsin (hepsi CLAUDE.md'den):

  ```python
  # scripts/architecture_check.py — iskelet
  RULES = [
      # (açıklama, glob, yasak regex, izinli istisnalar)
      ("ML kütüphanesi critical loop'ta yok",
       "src/custos/critical/**/*.py",
       r"^\s*(from|import)\s+(sklearn|numpy|scipy|torch|tensorflow|keras)",
       []),
      ("Collector'da asyncpg yok",
       "src/custos/critical/collector.py",
       r"(asyncpg|SELECT |INSERT |UPDATE |DELETE )",
       []),
      ("Modbus write fonksiyonu yok",
       "src/custos/**/*.py",
       r"\.write_(register|coil|registers|coils)\s*\(",
       []),
      ("datetime.now() timezone'suz yasak",
       "src/**/*.py",
       r"datetime\.now\(\s*\)",  # ()  içi boş = yasak
       []),
      ("datetime.utcnow() yasak",
       "src/**/*.py",
       r"datetime\.utcnow\(",
       []),
      ("print() yasak (structlog kullan)",
       "src/**/*.py",
       r"^\s*print\s*\(",
       []),
      ("Dashboard critical loop'u import etmiyor",
       "src/custos/analytics/**/*.py",
       r"from custos\.critical",
       []),
      ("Abstract DB arayüzü: SQL string src/*'de sadece shared/database.py",
       "src/custos/**/*.py",
       r"(SELECT |INSERT INTO|UPDATE |DELETE FROM)",
       ["src/custos/shared/database.py"]),
      # ...ek kurallar
  ]
  ```

- [ ] **1.3** — Şu komutla çalıştır, ihlalleri gör:
  ```bash
  wsl -e bash -c "cd /home/orientpro/projeler/custos && source .venv/bin/activate && python scripts/architecture_check.py"
  ```

- [ ] **1.4** — Her bulunan ihlali tek tek değerlendir. **Gerçek ihlal mi, false positive mi?** Gerçekse düzelt, değilse `# allow-arch-check: <sebep>` yorumuyla istisna işaretle (script bu yorumu görünce atlasın).

- [ ] **1.5** — Script `exit 0` dönmeye başlayınca `.pre-commit-config.yaml`'a ekle:
  ```yaml
  - repo: local
    hooks:
      - id: architecture-check
        name: Custos architecture rules
        entry: python scripts/architecture_check.py
        language: system
        pass_filenames: false
        always_run: true
  ```

- [ ] **1.6** — `.github/workflows/` altına CI job ekle (pre-commit zaten çağırıyorsa atla).

### Başarı kriteri
Tüm kurallar yeşil, pre-commit hook aktif, bir deneme ihlaliyle (örn. `src/custos/critical/foo.py` içine `import sklearn` koy) commit engelleniyor.

### Beklenen çıktı
`scripts/architecture_check.py` dosyası + güncellenen `.pre-commit-config.yaml` + varsa düzeltilen ihlaller.

---

## Adım 2 — Test coverage ölçümü

### Amaç
129 test var ama **hangi yüzeyi** kapatıyor bilmiyoruz. Kapatılmayan path'ler "sahada patlama adayı."

### Süre
2 saat (ölçüm 10 dk, rapor okuma + boşluk analizi 1.5 saat).

### Önkoşul
- `pytest-cov` kurulu (✅ `pyproject.toml` dev dependencies'te var)
- DB container ayakta (integration testler için)

### Adımlar

- [ ] **2.1** — Tüm testleri coverage ile çalıştır:
  ```bash
  wsl -e bash -c "cd /home/orientpro/projeler/custos && source .venv/bin/activate && docker compose up -d && pytest tests/ --cov=src/custos --cov-report=html --cov-report=term-missing"
  ```

- [ ] **2.2** — Terminal raporundan kritik modülleri gör. **Hedef eşikler:**
  | Modül | Hedef |
  |---|---|
  | `src/custos/critical/collector.py` | **≥85%** |
  | `src/custos/shared/database.py` | **≥85%** |
  | `src/custos/analytics/threshold_engine.py` | **≥85%** |
  | `src/custos/analytics/anomaly_detector.py` | **≥80%** |
  | `src/custos/analytics/kpi_engine.py` | **≥80%** |
  | `src/custos/analytics/dashboard/` | ≥60% (UI, tolere edilir) |
  | Genel toplam | **≥75%** |

- [ ] **2.3** — HTML raporu VS Code'da aç:
  ```bash
  wsl -e bash -c "cd /home/orientpro/projeler/custos && explorer.exe htmlcov/index.html"
  ```
  Kırmızı satırlara bak. **"Missing" sütunu** = test görmediği kod. Kritik modüllerde missing satırları listele.

- [ ] **2.4** — Her kapatılmayan kritik path için karar:
  - **Gerçekten test edilmeli** → test yaz
  - **Test edilemez (örn. network error handling)** → `# pragma: no cover` + yorum

- [ ] **2.5** — `pyproject.toml`'a minimum threshold ekle (regression engelle):
  ```toml
  [tool.coverage.report]
  fail_under = 75
  ```

- [ ] **2.6** — Eksik bulunan **en kritik 3 test senaryosunu** yaz (coverage göstergesi, önemli olan path'leri önceliklendir). Önerim: collector disconnect/reconnect path, DB pool exhaustion, threshold state transitions.

### Başarı kriteri
Genel coverage ≥75%, kritik modüller ≥85%, `pytest --cov` komutu threshold ihlalinde kırmızı verir.

### Beklenen çıktı
`htmlcov/` dizini + `pyproject.toml` güncellemesi + 2–3 yeni test.

---

## Adım 3 — Dependency zafiyet taraması

### Amaç
`pymodbus`, `asyncpg`, `fastapi`, `sentence-transformers` gibi network/parser katmanındaki kütüphaneler CVE barındırabilir. Pilot saha ağında duracak cihaz için zorunlu hijyen.

### Süre
30 dakika.

### Önkoşul
Yok.

### Adımlar

- [ ] **3.1** — `pip-audit` kur (sadece dev):
  ```bash
  wsl -e bash -c "cd /home/orientpro/projeler/custos && source .venv/bin/activate && pip install pip-audit"
  ```

- [ ] **3.2** — Tara:
  ```bash
  wsl -e bash -c "cd /home/orientpro/projeler/custos && source .venv/bin/activate && pip-audit --strict"
  ```

- [ ] **3.3** — Rapora bak. Bulgular için karar:
  - **HIGH/CRITICAL** → hemen güncelle (pyproject.toml'da sürümü kaldır veya üstüne çıkar)
  - **MEDIUM** → fix mevcutsa güncelle, yoksa workaround + takip notu
  - **LOW** → not al, v1.1'e bırak

- [ ] **3.4** — **Özel not:** `pymodbus<3.13.0` pinli (memory'deki SimData API geçişi). Bu pin nedeniyle güvenlik güncellemesi alamıyorsak, pilot öncesi 3.13 geçişini yeniden değerlendir.

- [ ] **3.5** — `pip-audit`'i CI'a ekle (opsiyonel, W3'te):
  ```yaml
  # .github/workflows/security.yml
  - run: pip-audit --strict
  ```

### Başarı kriteri
HIGH/CRITICAL bulgu yok. MEDIUM bulgular notlandı ve takipte.

### Beklenen çıktı
`docs/dependency_audit_2026_04_22.md` — bulgular ve kararlar.

---

## Adım 4 — Bağımsız kod incelemesi (subagent)

### Amaç
Claude (ben) bu projede çok çalıştım, **kör nokta riskim yüksek**. Bağımsız bir subagent sıfır context ile kritik modülleri inceler.

### Süre
4–6 saat (agent başına ~30 dk, değerlendirme 2 saat).

### Önkoşul
Adım 1 ve 2 tamam (script ve coverage raporu agent'a context olarak verilir).

### Adımlar

- [ ] **4.1** — Claude Code CLI'da şu komutu çalıştır (her dosya için ayrı bir agent, paralel):
  ```
  /agent code-reviewer
  ```
  ya da prompt ile:
  > `src/custos/critical/collector.py` dosyasını incele. Projeyi bilmiyormuş gibi davran. CLAUDE.md'deki kurallara (özellikle "sadece okur", abstract DB, datetime UTC) uygunluğu kontrol et. Sessiz yutulan exception, race condition adayı, memory leak pattern, tutarsız error handling, anlamsız abstraction varsa işaretle. Raporu markdown olarak döndür.

- [ ] **4.2** — Şu modülleri **ayrı ayrı** incelet:
  - [ ] `src/custos/critical/collector.py`
  - [ ] `src/custos/critical/__main__.py`
  - [ ] `src/custos/shared/database.py`
  - [ ] `src/custos/analytics/threshold_engine.py`
  - [ ] `src/custos/analytics/anomaly_detector.py`
  - [ ] `src/custos/analytics/kpi_engine.py`
  - [ ] `src/custos/analytics/push_sender.py`

- [ ] **4.3** — Her raporu `docs/code_review_<modül>_2026_04_22.md` olarak kaydet.

- [ ] **4.4** — Bulgular için karar tablosu (her bulgu için):
  | Bulgu | Kritiklik | Aksiyon | Ne zaman |
  |---|---|---|---|
  | Ör: collector'da `except Exception: pass` | Yüksek | Specific exception + log | Bu hafta |
  | Ör: kpi_engine AST parse edilen formülde overflow koruması yok | Orta | Input sanitization | W3 |

- [ ] **4.5** — **Yüksek** kritiklik bulguları pilot öncesi kapat. **Orta** v1.1'e bırakabilirsin ama iş planı B bölümüne not düş.

### Başarı kriteri
7 modül için rapor mevcut, yüksek kritiklik bulgu yok (ya da kapatıldı).

### Beklenen çıktı
`docs/code_review_*.md` dosyaları + karar tablosu.

---

## Adım 5 — Uzun süreli dayanıklılık testi ⭐

### Amaç
Pilot kabul kriteri "**14 gün kesintisiz**" — test ortamında 7 gün yeterli doğrulama (memory leak/pool exhaustion/tick miss ilk 48-72 saatte görünür, gün 3-7 monoton). Pilotta zaten 14 gün gerçek yük altında koşacak (5-19 Haziran).

### Süre
**Pasif 7 gün** — **feature-complete sonrası, 28 Mayıs başlangıç, 3 Haziran bitiş** (4 Haz Go/No-Go günü rapor hazır). Aktif çalışma 2–3 saat (kurulum + izleme + rapor).

### Önkoşul
- Simülatör 200 tag gerçek profiliyle güncellenmiş (W5'te saha keşfinden gelen register haritası)
- Boş bir makine (ikinci mini PC, eski laptop, Raspberry Pi 4+ ya da WSL bırakılabilir)

### Adımlar

- [ ] **5.1** — Simülatörü 200 tag + karışık polling (örn. 150 slow, 45 normal, 5 fast) üretecek şekilde genişlet. Mevcut: `src/custos/simulator/modbus_server.py`.

- [ ] **5.2** — Test hedef makinesini hazırla. Systemd service'i kur:
  ```bash
  wsl -e bash -c "sudo bash /path/to/custos/deploy/setup.sh"
  ```

- [ ] **5.3** — Metrik toplayıcı script yaz: `scripts/endurance_metrics.py`. 5 dakikada bir şunu yazsın (`logs/endurance.csv`):
  - timestamp
  - RSS memory (collector process) — `ps -o rss= -p <pid>`
  - DB connection count — `SELECT count(*) FROM pg_stat_activity`
  - `tag_readings` satır sayısı
  - son 5 dk tick miss oranı (collector log'dan grep)
  - disk kullanımı (`df` output)

- [ ] **5.4** — Başlat:
  ```bash
  wsl -e bash -c "cd /path/to/custos && source .venv/bin/activate && nohup python scripts/endurance_metrics.py > logs/endurance.log 2>&1 &"
  ```

- [ ] **5.5** — **Her gün** (sabah 5 dk): CSV'yi kontrol et, grafik oluştur (`scripts/endurance_plot.py`). **Kırmızı bayraklar:**
  - RSS memory **lineer artıyor** → memory leak
  - DB connection count **tavana dayandı** → pool exhaustion
  - Tick miss oranı **gün geçtikçe artıyor** → queue birikimi
  - Disk **retention'a rağmen büyüyor** → retention job çalışmıyor

- [ ] **5.6** — 3. günde ara kontrol (RSS memory trendi, pool durumu). Kırmızı bayrak varsa hemen fix.

- [ ] **5.7** — 7. günde (3 Haziran) final rapor: `_personal/pilot/endurance_test_report_2026_06_03.md`.

### Başarı kriteri
- **7 gün kesintisiz**, 0 crash
- RSS memory düz (ilk 24 saatlik warm-up hariç)
- DB connection count stabil
- Tick miss oranı **< %1** (ilk gün ile son gün aynı seviye)
- Disk retention'a uygun hareket ediyor (7 gün compression policy tam cycle görünür)

### Beklenen çıktı
7 günlük CSV + grafik + rapor (kişisel, `_personal/pilot/` içinde).

---

## Adım 6 — Chaos/recovery testleri

### Amaç
Sahada kaçınılmaz: network kesintisi, güç kesintisi, disk dolması. Her senaryoda **graceful** mi **crash** mi?

### Süre
1 gün.

### Önkoşul
Adım 5'in ortasında ya da sonrasında yapılabilir (aynı ortam).

### Adımlar

Her senaryo için: **Başlat → Bozuntu uygula → 5 dk gözlem → Sistem iyileşti mi?**

- [ ] **6.1** — **Simülatör çökmesi**
  ```bash
  # simülatörü durdur
  pkill -f "custos.simulator"
  # 60 sn bekle, tekrar başlat
  sleep 60 && python -m custos.simulator &
  # kontrol: collector log'da "Modbus bağlantısı kuruldu" tekrar görünmeli
  ```
  **Kabul:** Collector 3 dk içinde otomatik reconnect.

- [ ] **6.2** — **DB çökmesi**
  ```bash
  docker compose restart timescaledb
  # kontrol: dashboard 503/500 değil; collector 2 dk içinde pool yeniden açmalı
  ```
  **Kabul:** Dashboard "DB geçici olarak kapalı" mesajı gösterir, çökmez. Collector recovery.

- [ ] **6.3** — **Disk doluluk**
  ```bash
  # test için 2 GB'lik bir dosya oluştur (quota yoksa)
  dd if=/dev/zero of=/tmp/fill.bin bs=1M count=2048
  # Settings'te retention auto-clean aktifse diskte yer açılmalı
  ```
  **Kabul:** %85'te Web Push uyarısı geldi (F11 Paket F), %95'te retention agresifleşti.

- [ ] **6.4** — **Collector kill -9**
  ```bash
  pkill -9 -f "custos.critical"
  # systemd restart davranışı
  sleep 5 && systemctl status custos-critical
  ```
  **Kabul:** Systemd 5 saniye içinde restart etti, veri kaybı < 1 dk.

- [ ] **6.5** — **Ağ kesintisi (simüle)**
  ```bash
  # iptables ile 5020 portunu 60sn blok et
  sudo iptables -A OUTPUT -p tcp --dport 5020 -j DROP
  sleep 60
  sudo iptables -D OUTPUT -p tcp --dport 5020 -j DROP
  ```
  **Kabul:** Collector timeout'ta `quality_flag=1` ile bayrak atar, yeniden bağlanınca devam eder.

- [ ] **6.6** — **Güç kesintisi (mini PC)**
  - Fiziksel olarak kabloyu çek (ya da VM'de force poweroff)
  - Tekrar boot et
  - **Kabul:** Tüm servisler (DB, collector, dashboard) otomatik başladı, data kaybı < 5 dk.

- [ ] **6.7** — Her senaryoyu `docs/chaos_test_2026_XX.md`'de raporla: komut, beklenti, sonuç, geçti/kaldı.

### Başarı kriteri
6 senaryonun en az **5'i geçti**. Geçmeyen senaryo için hotfix W6'ya girer.

### Beklenen çıktı
`docs/chaos_test_*.md` raporu.

---

## Adım 7 — Deploy dry-run (temiz VM/PC)

### Amaç
`deploy/setup.sh` + systemd service **hiç kullanılmamış bir makinede** çalışıyor mu? Dokümantasyon boşluğu, eksik env var, izin sorunu — sahada değil, kontrollü bir yerde çıkmalı.

### Süre
1 gün.

### Önkoşul
Temiz bir Ubuntu 22.04 ortamı (VM, VirtualBox/VMware/WSL Ubuntu fresh install, ikinci mini PC).

### Adımlar

- [ ] **7.1** — Temiz Ubuntu 22.04 kur (VM ya da fiziksel).

- [ ] **7.2** — Sadece `deploy/README_PILOT.md`'ye **harfiyen uyarak** kurulumu yap. **Belgeye yazılmayan hiçbir adım atma** — atlamak zorunda kaldığın her şey boşluktur, dokümanı güncelle.

- [ ] **7.3** — Adım adım not al:
  - [ ] Repo klonlandı mı? Nereye? Hangi izinle?
  - [ ] `.env` nasıl oluşturuldu? Hangi değerler belgede var, hangi değerler kullanıcıdan istenmeli?
  - [ ] Docker kurulumu belgede var mı?
  - [ ] `deploy/setup.sh` hatasız çalıştı mı?
  - [ ] `alembic upgrade head` sorunsuz mu?
  - [ ] `systemctl status custos-*` tüm servisler `active` mi?
  - [ ] `http://localhost:8000/dashboard` tarayıcıda açıldı mı?
  - [ ] VAPID key üretildi mi? Push test başarılı mı?

- [ ] **7.4** — Her takıldığın yerde iki iş yap: **(a) geçici çözümle ilerle, (b) `deploy/README_PILOT.md`'ye satır ekle.**

- [ ] **7.5** — İkinci bir kere **sıfırdan** yap (aynı VM snapshot'ı sil, yeniden başla). Bu sefer 0 takılma hedef. Takılırsan dokümantasyon hala eksik.

- [ ] **7.6** — Rapor: `docs/deploy_dry_run_2026_XX.md`.

### Başarı kriteri
İkinci tur sıfırdan kurulum **30 dakika altında**, dokümanda tanımlı olmayan hiçbir komut çalıştırılmadan tamamlandı.

### Beklenen çıktı
Güncellenen `deploy/README_PILOT.md` + dry-run raporu.

---

## Genel takvim (22 Nisan 2026 revize)

```
W2 (23–29 Nis):         [Adım 1 ✅] [Adım 2 ✅] [Adım 3 ✅]  ← W2 denetim tamam
W3-W5 (30 Nis–20 May):  Feature işleri — F9 + F10 + Paket I + Saha 1
W6 (21–27 May):         Saha 2 + [Adım 4 subagent] + [Adım 6 chaos] + kılavuz
Feature freeze:         27 Mayıs akşam
W7 (28 May–3 Haz):      [Adım 5 — 7 gün endurance pasif] + [Adım 7 dry-run] + A4 fix + (ops.) coverage boost
4 Haziran:              Adım 5 final rapor + Go/No-Go
5 Haziran:              PILOT GO-LIVE
```

Gerekçe: **"Önce fix/teknik borç → sonra test"**. Yazılmamış kod üzerinde denetim yapmak boşa iş; feature-complete koda A4+A5+A6+A7 daha doğru bulgu verir.

---

## Sonuç raporu

Denetim tamamlandığında şu dosyalar olmalı:

- [ ] `scripts/architecture_check.py` (Adım 1)
- [ ] `htmlcov/` güncel (Adım 2)
- [ ] `docs/dependency_audit_2026_04_22.md` (Adım 3)
- [ ] `docs/code_review_*.md` × 7 modül (Adım 4)
- [ ] `docs/endurance_test_report_2026_05_XX.md` (Adım 5)
- [ ] `docs/chaos_test_2026_XX.md` (Adım 6)
- [ ] `docs/deploy_dry_run_2026_XX.md` (Adım 7)
- [ ] Güncellenen `deploy/README_PILOT.md` (Adım 7)
- [ ] Güncellenen `.pre-commit-config.yaml` (Adım 1)

### Go/No-Go kararı (5 Haziran öncesi son gün — 4 Haziran)

| Kriter | Durum |
|---|---|
| Mimari kural denetimi yeşil | [ ] |
| Coverage ≥%75 (kritik ≥%85) | [ ] |
| HIGH/CRITICAL bağımlılık açığı yok | [ ] |
| Subagent incelemesi yüksek kritiklik bulgu kapatıldı | [ ] |
| 14 günlük dayanıklılık testi geçti | [ ] |
| 6 chaos senaryosundan 5+ geçti | [ ] |
| Deploy dry-run 2. tur 30 dk altında bitti | [ ] |

Hepsi ✅ → **GO**. Bir tanesi ❌ → pilot ertelemesi konuşulur.

---

## Değişiklik notları

- **2026-04-22** — Adım 1 tamamlandı: `scripts/architecture_check.py` 11 kuralla
  (ML critical, asyncpg collector, SQL collector, Modbus write, datetime.now naive,
  datetime.utcnow, print, analytics→critical, SQL shared/database dışında, deep learning,
  asyncpg dışı DB driver) devrede. Pre-commit local hook + CI step (ruff'tan önce) aktif.
  `# allow-arch-check: <sebep>` istisna yorumu destekleniyor. Baseline 11/11 yeşil.
- **2026-04-22** — Adım 2 tamamlandı: `pytest --cov` baseline %66.50 (collector %93,
  database %89, threshold_engine %75, scanner %86, archiver %94). 3 yeni kritik
  test path'i: `tests/unit/test_collector_read_paths.py` (5 test: connect fail,
  response error, exception, gain+offset success, refresh schedule),
  `tests/integration/test_threshold_engine.py` (+2 test: low direction breach+clear,
  reading silinince debounce tracker temizliği). `pyproject.toml` `[tool.coverage.report]
  fail_under = 65` regression guard. **Hedef pilot öncesi %75 — yükseltme planı:**
  W3 → 70 (anomaly_detector + kpi_engine'e unit test), W5 → 75 (dashboard route
  coverage). Açık modüller (W3+ kapsamı): `anomaly_detector.py` %13,
  `kpi_engine.py` %32, `analytics/dashboard/app.py` %37. Toplam test 331 passed,
  8 skipped, 0 fail.
- **2026-04-22** — Adım 3 tamamlandı: `pip-audit` v2.10.0 ile tarama, 4 bulgu /
  3 paket (1 skip torch+cpu PyPI'da yok). 2 HIGH (`lxml 6.0.4 → 6.1.0`
  CVE-2026-41066 XXE; `setuptools 70.2.0 → 78.1.1` CVE-2025-47273 path traversal),
  1 belirsiz severity (`transformers 4.57.6 → 5.0.0rc3` CVE-2026-1839,
  RC sürüm + sentence-transformers ailesi major bump gerektirir). Detaylı
  rapor + aksiyon önerisi `_personal/pilot/dependency_audit_2026_04_22.md`
  (pyproject/setup.sh değişiklikleri kullanıcı onayı bekliyor).
  `pymodbus<3.13.0` pin nedeniyle alınamayan CVE **YOK** (3.10.x güncel).
  `transformers` v1.1'e ertelendi (RC + chatbot ailesi rework riski).
- **v1.1 için açık kalemler:**
  - `pyproject.toml` bağımlılık beyaz liste denetimi `architecture_check`'e ekle.
    Şu an sadece kod düzeyi import'ları kapsıyor; yeni bir DL/ORM kütüphanesi
    pyproject'e eklenip henüz import edilmemişse script yakalamaz.
  - F8b transformers 5.x + sentence-transformers 4.x geçişi (CVE-2026-1839)
  - CI'a `pip-audit --strict` step (HIGH bulgular kapatıldıktan sonra Adım 3.5)
  - Coverage hedefini W3'te 70'e, W5'te 75'e yükselt (anomaly_detector + kpi_engine
    + dashboard route testleri).
