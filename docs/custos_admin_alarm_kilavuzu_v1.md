# Custos Admin Alarm Kılavuzu

**Sürüm:** v1.0
**Tarih:** 2026-05-01
**Hedef kitle:** Sistem yöneticisi, IT, entegratör — sistemi *kuran ve ayarlayan* kişi.
**Operatör için ayrı kılavuz:** [`custos_operator_kilavuzu_v1.md`](custos_operator_kilavuzu_v1.md)

Bu doküman alarm sisteminin tamamı için referans niteliğindedir. Operatör
kılavuzu "ne yapacağım"ı, bu kılavuz "nasıl çalışıyor + nasıl ayarlanır"ı
anlatır.

---

## 1. Mimari özet

Custos iki bağımsız süreçten oluşur:

- **Critical loop** (`src/custos/critical/`) — Modbus okur, DB'ye yazar. ML
  yok, dashboard yok. Tek alarm-ilgili katkısı: kendi heartbeat'ini DB'ye
  yazar (analytics watchdog'u bunu okur). Modbus client salt okuma; yazma
  fonksiyonu YOK.
- **Analytics loop** (`src/custos/analytics/`) — Bütün alarm motorları,
  push gönderici, dashboard, escalation burada. Critical'a bağımlı değil:
  Critical kapansa bile analytics çalışır, sadece "watchdog: critical down"
  alarmı tetiklenir.

**Veri akışı (alarm açısından):**

```
PLC/Sensör → Modbus → critical/collector → readings tablosu (TimescaleDB)
                                              ↓
        ┌───── 7 alarm motoru (analytics) periyodik okur ─────┐
        │                                                       │
   threshold_engine        liveness_engine   spc_engine   anomaly_detector
   (threshold +            (30 sn tick)      (5 dk tick)   (5 dk inference)
    rate_of_change +
    cross_sensor)
        │                          │              │              │
        └────── alarm_events tablosu (INSERT) ──────────────────┘
                              ↓
                  push_sender → Web Push (VAPID)
                              ↓
                  escalation (60 sn tick) — warn → crit yalnız kullanıcı kaynakları
                              ↓
                  Dashboard (alarms.html, overview.html)
                              ↓
                  Operatör → acknowledge / etiketle / cleared
```

---

## 2. Alarm kaynakları (7 motor)

`alarm_events.source` kolonu, alarmın hangi motordan geldiğini söyler. UI
"Tip" filtresi bu alanı kullanır.

### 2.1 `threshold` — Kullanıcı tanımlı eşik

**Tetikleyici:** [threshold_engine.py](../src/custos/analytics/threshold_engine.py),
5 saniyede bir tick. Aktif `thresholds` tablosu satırlarını okur, her tag
için son okumayı (`get_latest_tag_readings`) eşikle karşılaştırır.

**Kullanıcı tanımı (UI: Eşikler sayfası):**
- `tag_id` — hangi tag
- `set_point` — eşik değeri
- `direction` — `high` (≥ ise alarm) / `low` (≤ ise alarm)
- `hysteresis` — alarm temizlemek için ölü bant (örn. set_point 80, hyst 5
  → 75 altına düşmeden temizlenmez)
- `debounce_seconds` — alarm yazılmadan önce eşiğin bu kadar süre tutması
  gerekir; gürültü filtresi
- `severity` — `info` / `warn` / `crit` / `emergency` (kullanıcı seçer)

**Özel davranışlar:**
- `severity == emergency` → debounce override (max 1 sn beklenir) + auto-clear
  bypass (operatör manuel acknowledge etmek zorunda).
- Bakım modu (`is_test=True`) — alarm yazılır ama push gönderilmez.

### 2.2 `rate_of_change` — Hızlı değişim

**Tetikleyici:** Aynı tick'in içinde
([threshold_engine.py:_check_rate_of_change](../src/custos/analytics/threshold_engine.py:364)).
Tag tablosundaki `rate_of_change_threshold` kolonu (birim: birim/dakika)
doluysa, son iki okuma arası `|delta/dt|` bu eşiği aşarsa alarm.

**Sabitler:** Cooldown 5 dk, dt < 1 sn ise sayısal hata büyük → atla.
**Severity:** Hep `warn` (kod sabit).

### 2.3 `cross_sensor` — İki tag arası mantıksal kural

**Tetikleyici:** Aynı tick
([threshold_engine.py:_check_cross_sensor_rules](../src/custos/analytics/threshold_engine.py:449)).
`cross_sensor_rules` tablosu — kural: `value_a OP value_b` (op: `lt/gt/eq/neq/lte/gte`).
Kural sağlanmıyorsa alarm.

**Kullanıcı tanımı (UI: Cross-sensor sayfası):** kural adı, tag A, operator,
tag B, severity (kullanıcı seçer).

**Sabitler:** Cooldown 10 dk.

### 2.4 `liveness` — Sensör donuk

**Tetikleyici:** [liveness_engine.py](../src/custos/analytics/liveness_engine.py),
30 saniyede bir. Her aktif tag için son 2 saatlik okumalara bakar.

**İki mod:**
- **Stuck-at**: Son okunan değer X saniyedir hiç değişmediyse alarm.
  Tag preset'i (`stuck_at_preset`: fast/normal/slow/very_slow/none/counter/sayaç)
  veya tag-bazlı override süreyi belirler.
- **Counter**: `stuck_at_preset='counter'` ise — değer azaldıysa (sayaç
  geri gitti) veya pencere boyu hiç artmadıysa alarm.

**Sabitler:** Cooldown 1 saat. **Severity:** Hep `warn`. Kaynak: kod sabit
(`liveness_engine.py:190`).

**v1.0.1 borç:** Counter için rollover-aware mantık eksik. uint16 (65535)
sınırına gelince sayaç 0'a sarar; `Counter geri gitti` sahte alarm tetiklenir.
Drift simulator'da `_energy_step` 0.05/tick'e düşürülerek demo penceresinde
rollover engellendi (commit `d4ae0f0`), ama gerçek meter için engine'in
`prev > 60000 AND new < 5000` rollover kabul etmesi gerek.

### 2.5 `spc` — Statistical Process Control

**Tetikleyici:** [spc_engine.py](../src/custos/analytics/spc_engine.py),
5 dakikada bir. `spc_enabled=true` tag'lerin ilk 100 örneğinde sessiz
öğrenme (median + MAD), sonra 3 paralel test:

- **EWMA**: `ewma_t = 0.2*x + 0.8*ewma_{t-1}`. Alarm: `|x - ewma| > 3*sqrt(var)`.
- **CUSUM**: Kümülatif sapma 5σ aşarsa alarm.
- **MAD-score**: `|x - median| / (1.4826*MAD) > 3.5` ise alarm (robust z).

**Sabitler:** Cooldown 30 dk. **Severity:** Hep `warn`.

### 2.6 `anomaly` — Isolation Forest (ML)

**Tetikleyici:** [anomaly_detector.py](../src/custos/analytics/anomaly_detector.py).
Asset instance bazında offline eğitilmiş `.joblib` modeli yükler, son tag
okumalarından feature vektörü oluşturup skor hesaplar. Skor eşik altıysa
alarm.

**Eğitim:** Cihazda DEĞİL, geliştirici makinesinde
(`scripts/train_anomaly_models.py`). Pilot kurulumunda model dosyaları
`models/` klasöründe deploy edilir. Bakım modunda (`is_test=True`) toplanan
veri eğitim setinden filtrelenir (`ANOMALY_SUPPRESSED_MODES`).

**Severity:** Hep `warn`.

### 2.7 `watchdog` — Servis sağlığı

**Tetikleyici:** [heartbeat.py](../src/custos/analytics/heartbeat.py).
Her servis kendi heartbeat'ini DB'ye yazar (`service_heartbeats` tablosu);
analytics 60 sn'de bir tüm heartbeat'leri okur:

- ≤60 sn: `healthy` (yeşil)
- 60-180 sn: `stale` (sarı, geçici tıkanma)
- \>180 sn veya hiç heartbeat yok: `down` (kırmızı, alarm)

**Severity:** `crit` (kod sabit) — servis 3 dk down'sa zaten kritik.

---

## 3. Severity ve escalation politikası

### 3.1 4-tier severity (V11-107/K10)

| Tier | Renk | Anlam | Push | Sessiz saat | Auto-clear |
|---|---|---|---|---|---|
| `info` | gri | Bilgi | Operatöre göre | Geçerli | Var |
| `warn` | sarı | Dikkat | Operatöre göre | Geçerli | Var |
| `crit` | kırmızı | Kritik | Operatöre göre | Geçerli | Var |
| `emergency` | vurgulu kırmızı | Hayati/operasyonel risk | Operatöre göre | **Bypass** | **Yok** (manuel zorunlu) |

### 3.2 Escalation kuralı (1 May 2026 — kullanıcı kuralı)

[escalation.py](../src/custos/analytics/escalation.py) 60 sn'de bir tarar.
warn alarmları, `escalation_warn_to_crit_minutes` (Settings → Veri Saklama,
default 30, range 5-240) süresinden uzun açık kalmışsa crit'e yükseltir.

**Önemli kısıt:** Sadece **kullanıcı tanımlı kaynaklar** yükseltilir:
`source IN {threshold, cross_sensor}`. Otomatik kaynaklar (liveness,
anomaly, spc, watchdog, rate_of_change) escalate EDİLMEZ — warn'da kalır.

**Neden:** Kullanıcı kuralı — "Critical sadece operatörün kendi belirlediği
eşikler ve sistemler üzerinde olur." Otomatik kaynaklar gürültü riski
taşır; bunlar warn olarak kalır, operatör elle acknowledge eder.

**Yan etkiler:**
- `is_test=True` (bakım modu) alarmı escalate edilmez.
- `escalated_from` doluysa tekrar dokunulmaz (idempotent).
- Yükseltme audit log'a yazılır (`category='alarm_escalation'`).
- Push `crit` kanalı üzerinden gider (sessiz saatte tutulabilir).

### 3.3 Pratik severity stratejisi (admin için)

| Operasyonel risk | Önerilen tier | Örnek |
|---|---|---|
| Hayati/operasyonel: gaz kaçağı, FIRE_ALARM | `emergency` | `GAS_LEAK_DETECTED` |
| Üretim/komfor doğrudan etkilenir | `crit` | Chiller kapalı |
| Trend kötüye gidiyor, mühendis bakmalı | `warn` | Filtre DP yükseliyor |
| Yalnızca log/raporlama | `info` | Setpoint değişti |

Otomatik motorlar (liveness/spc/anomaly) hep `warn` üretir — bu doğru.
Operatör onları acknowledge eder; gerçekten ciddi olan otomatik bulguları
manuel olarak crit threshold'una çevirmek istiyorsa o tag için ayrı bir
threshold tanımlar.

---

## 4. State machine (ISA-18.2)

```
   [yok]
     │
     │ (eşik aşıldı + debounce + bakım kontrolü)
     ▼
  triggered ─────────► acknowledged ─────────► cleared
     │  ▲                  │                       ▲
     │  │ (eşik tekrar      │ (eşik düştü +         │
     │  │  aşılırsa)        │  hysteresis +         │
     │  │                   │  emergency hariç)     │
     │  └───────────────────┘                       │
     │                                              │
     └──── (escalation 30 dk → crit, source         │
            ∈ {threshold, cross_sensor}) ───────────┘
```

**Kolonlar:**
- `state`: `triggered` / `acknowledged` / `cleared`
- `triggered_at` / `acknowledged_at` / `cleared_at`
- `trigger_value` / `clear_value`
- `escalated_from` / `escalated_at` (yükseltme izi)
- `is_test` — bakım modunda atıldıysa True

**Operatör akışı:**
1. Push gelir veya dashboard'da görür.
2. **Acknowledge** — sorunla ilgilendiğini bildirir; alarm zaman damgası tutulur.
3. **Etiketle** (R-05) — `gercek_ariza` / `yanlis_alarm` / `bakim_sirasinda` / `bilinmiyor`. Re-label upsert; geçmişi audit log tutar.
4. Sorun düzelirse → **cleared** (otomatik, hysteresis aşıldıktan sonra).
5. Emergency'de → manuel cleared (auto-clear yok).

---

## 5. Push notification (Web Push, VAPID)

### 5.1 Akış

[push_sender.py](../src/custos/analytics/push_sender.py):

1. **Master switch** (`settings.push_enabled`) — kapalıysa erken çıkış.
2. Aktif `push_subscriptions` çek.
3. Her abonelik için `_should_notify(sub, severity, now_time)`:
   - `sub.enabled = False` → atla
   - `severity == emergency` → sessiz saat bypass, sadece `notify_emergency` boole'una bakar
   - Diğer tier: sessiz saat içindeyse atla; yoksa `notify_warn/crit/info`'ya bakar
4. VAPID imzalı `webpush()` çağrısı.
5. HTTP 410 (Gone) → abonelik silinir (cihaz unregister olmuş).

### 5.2 Konfigürasyon

`.env` (veya pilot deploy'da `.env.endurance`):

```
CUSTOS_VAPID_PUBLIC_KEY=...   # frontend applicationServerKey
CUSTOS_VAPID_PRIVATE_KEY=...  # backend imzalama
```

VAPID anahtar üretimi: [`scripts/b4_generate_vapid.py`](../_personal/endurance/b4_generate_vapid.py)
ya da pilot setup script'i.

### 5.3 Abone tercihleri (Settings → Bildirimler)

`push_subscriptions` tablosunda her cihaz için:
- `enabled` (master, tek-tıkla sustur)
- `notify_info / notify_warn / notify_crit / notify_emergency` (4 boole)
- `quiet_start / quiet_end` (sessiz saat aralığı; gece yarısını geçen aralıklar desteklenir)

---

## 6. Bakım modu (maintenance — P-04)

### 6.1 Konsept

Bir sensörü değiştirirken/kalibre ederken sahte alarmları susturmak için.

İki seviye:
- **Per-instance**: `asset_instances` tablosunda `is_in_maintenance` ve süre.
  Tag bir instance'a bind'liyse ve instance bakım modundaysa → alarm
  `is_test=True` yazılır.
- **Global**: `global_maintenance_window` — bütün sistem için. Tipik
  yıllık bakım için.

### 6.2 Etkileri

`is_test=True` olan alarm:
- DB'ye yazılır (geçmiş tutulsun, raporlanabilsin).
- Push **GÖNDERİLMEZ** ([push_sender.py:_should_notify](../src/custos/analytics/push_sender.py:39) öncesi early-return).
- Escalation'a girmez.
- ML eğitim setinden filtrelenir (`ANOMALY_SUPPRESSED_MODES` — anomaly_detector).
- Audit log kategorisi `maintenance_test_alarm` (operasyonel `alarm`'dan ayrı).

### 6.3 Operatör için (UI)

Settings → Bakım Modu sayfası: enstrüman/cihaz başına aç-kapa, süre belirle.
Süre dolunca otomatik kapanır. Manuel kapatma da var.

---

## 7. Etiketleme (R-05 / V11-301)

### 7.1 Sınıflar

| Sınıf | Renk | Emoji | Kullanım |
|---|---|---|---|
| `gercek_ariza` | kırmızı | 🔴 | Layer 1 doğru tetikledi, gerçek sorun var |
| `yanlis_alarm` | sarı | 🟡 | False positive |
| `bakim_sirasinda` | gri | 🔧 | Bakım sırasında oluştu |
| `bilinmiyor` | gri | ❓ | Operatör emin değil |

### 7.2 Veri kullanımı

`alarm_event_labels` tablosu: UNIQUE(`alarm_event_id`) — her alarm için
tek aktif etiket. Re-label upsert; audit log geçmişi tutar.

**Pilot süresince:** Etiketler birikir. Pilot kabul sonrası v1.1
(V11-303 Shadow mode + Auto retraining) bu etiketleri:
- Anomaly modeli için ground truth
- Threshold engine'in optimal eşiklerini öneren feedback loop'u

için kullanır.

---

## 8. Konfigürasyon: nereye dokunulur

| Ne | Nerede | Kim değiştirir |
|---|---|---|
| Eşik tanımları | UI: Eşikler sayfası → `thresholds` tablo | Operatör/admin |
| Cross-sensor kuralları | UI: Cross-sensor sayfası → `cross_sensor_rules` tablo | Operatör/admin |
| Tag stuck-at preset / rate_of_change_threshold | UI: Tags sayfası → `tags` tablosu kolonları | Admin |
| Tag SPC açma/kapama | UI: Tags sayfası → `tags.spc_enabled` | Admin |
| Anomaly modeli | `models/<instance>.joblib` (offline eğitim) | Geliştirici |
| Escalation süresi (warn→crit) | UI: Settings → Veri Saklama → `escalation_warn_to_crit_minutes` (5-240 dk) | Admin |
| Bildirim tercihleri | UI: Settings → Bildirimler → `push_subscriptions` | Her cihaz/operatör kendisi |
| Bakım modu (per-instance) | UI: Bakım Modu sayfası → `asset_instances.is_in_maintenance` | Operatör |
| Bakım modu (global) | UI: Settings → Global Bakım → `global_maintenance_window` | Admin |
| Disk uyarı eşiği | Kod sabit (`disk_telemetry.ALERT_THRESHOLD_PERCENT = 85`) | Geliştirici |
| Watchdog stale/down eşikleri | Kod sabit (`heartbeat.WARN_/CRIT_THRESHOLD_SECONDS = 60/180`) | Geliştirici |
| Liveness/SPC cooldown | Kod sabit (1 saat / 30 dk) | Geliştirici |
| VAPID anahtarları | `.env` → `CUSTOS_VAPID_*` | Admin (kurulum) |

---

## 9. Operasyonel kontroller

### 9.1 Servisler ayakta mı?

```bash
sudo systemctl status custos-critical custos-analytics
```

Pilot deploy birim adları: `custos-critical.service`, `custos-analytics.service`.
Endurance test ortamında: `custos-endurance-*.service` ailesi.

### 9.2 Hangi loop'lar başladı mı? — log filtresi

```bash
sudo journalctl -u custos-analytics -n 200 --no-pager | \
  grep -E "Threshold engine başlatıldı|Liveness engine başlatıldı|Escalation loop başlatıldı|SPC engine başlatıldı|Disk monitor başlatıldı"
```

Beş satır görmen lazım. Yoksa o motor başarısız.

### 9.3 Son tick'ler hata vermedi mi?

```bash
sudo journalctl -u custos-analytics -p err -n 100 --no-pager
```

Beklenen: Boş veya seyrek "geçici hata" satırları. Sürekli aynı hatadan
satır geliyorsa logu incele (motor adı `logger_name` kolonunda).

### 9.4 Push aboneleri ve son giderler

```sql
SELECT id, enabled, notify_emergency, quiet_start, quiet_end,
       last_seen_at, created_at
FROM push_subscriptions ORDER BY last_seen_at DESC NULLS LAST;
```

### 9.5 Aktif alarm sayımı (kaynak/severity bazlı)

```sql
SELECT source, severity, count(*)
FROM alarm_events
WHERE cleared_at IS NULL
GROUP BY source, severity ORDER BY 1, 2;
```

### 9.6 Escalation sağlık çek

```sql
SELECT count(*) FILTER (WHERE escalated_from IS NOT NULL) AS escalated,
       count(*) FILTER (WHERE escalated_from IS NULL AND severity='warn'
                          AND age(now(), triggered_at) > interval '30 min'
                          AND source IN ('threshold','cross_sensor'))
         AS overdue_eligible
FROM alarm_events WHERE cleared_at IS NULL;
```

`overdue_eligible > 0` ise escalation loop tıkanmış olabilir — log'a bak.

---

## 10. Sorun giderme

### 10.1 "Critical olmayan alarmlar critical etiketli"

**Belirti:** Liveness/SPC/Anomaly alarmı Aktif Alarmlar tablosunda
"Critical" gözüküyor.

**Sebep:** Eski escalation kuralı (30+ dk warn → crit) source ayrımı
yapmadan tüm warn alarmları yükseltiyordu.

**Çözüm (1 May 2026 deploy):** [escalation.py:_ESCALATABLE_SOURCES](../src/custos/analytics/escalation.py)
whitelist'i sadece `threshold` ve `cross_sensor`'u içerir. Mevcut yanlış
yükseltilmiş alarmları geri çevirmek için (manual, dikkatli):

```sql
UPDATE alarm_events
SET severity = escalated_from,
    escalated_from = NULL,
    escalated_at = NULL
WHERE severity = 'crit'
  AND escalated_from IS NOT NULL
  AND source IN ('liveness','anomaly','spc','watchdog','rate_of_change');
```

Audit log'a temizleme izi bırakmak için ayrıca insert et.

### 10.2 "Push gelmiyor"

Sırayla kontrol:

1. `settings.push_enabled` true mu? (Settings → Bildirimler)
2. VAPID anahtarları .env'de? `is_push_enabled()` true dönüyor mu?
3. Cihaz Settings → Bildirimler'de subscribe oldu mu? (`push_subscriptions`'da satır)
4. `sub.enabled = true` ve severity için tier boole'u açık mı?
5. Sessiz saat aralığında mıyız? (emergency hariç)
6. Alarmda `is_test=True` mı? (bakım modu push atlatır — beklenen davranış)
7. Browser geri planda mı? Service worker kayıtlı mı?
8. journalctl'de `Push gönderilemedi` veya HTTP 410 var mı? 410 abonelik
   silindi demektir, tekrar abone ol.

### 10.3 "Alarm kapanmıyor"

- Hysteresis bandı içinde mi? `set_point ± hysteresis` aralığından çıkması gerekir.
- Severity `emergency` mi? Auto-clear yok, manuel acknowledge zorunlu.
- Tag son okuması güncel mi? (Liveness alarmı zaten varsa eski değer "geliyor" gibi).

### 10.4 "Stuck-at sahte alarmı (sayaç tag'ı)"

- Tag `stuck_at_preset='counter'` mı? Counter mantığı uint16 rollover'a
  duyarsız (v1.0.1 borç). Kısa vadede artış hızını düşür veya engine'in
  rollover-aware versiyonunu bekle.

### 10.5 "Escalation 30 dk geçmesine rağmen alarm warn'da"

İki olası sebep:
1. `source` whitelist dışı (liveness/spc/anomaly/watchdog/rate_of_change) →
   beklenen davranış, warn'da kalır.
2. `is_test=True` (bakım modu) → escalate edilmez, beklenen.
3. `escalation_warn_to_crit_minutes` çok büyük olabilir (Settings → Veri Saklama).

---

## 11. Tablolar (referans)

| Tablo | İçerik |
|---|---|
| `thresholds` | Kullanıcı eşikleri |
| `cross_sensor_rules` | İki tag arası kurallar |
| `tags` | Tag tanımı + `rate_of_change_threshold` + `stuck_at_preset` + `spc_enabled` |
| `alarm_events` | Tüm alarm event'leri (7 source) |
| `alarm_event_labels` | R-05 etiketleri (4 sınıf) |
| `audit_log` | Tüm kategoriler — `alarm`, `alarm_emergency`, `alarm_escalation`, `maintenance_test_alarm` |
| `service_heartbeats` | Watchdog |
| `push_subscriptions` | Web Push abonelikleri + tier tercihleri |
| `asset_instances` | Bakım modu kolonları (per-instance) |
| `global_maintenance_window` | Bakım modu (global) |
| `retention_config` | Singleton — `escalation_warn_to_crit_minutes` |
| `spc_state` | SPC öğrenme + drift state (tag başına) |
| `anomaly_scores` | Inference skorları |

---

## 12. v1.0.1 / v1.1 alarm-ilgili tech debt

- **v1.0.1 (pilot öncesi):** Liveness counter rollover-aware mantık
  ([liveness_engine.py](../src/custos/analytics/liveness_engine.py) — uint16 sınırında geri-gitti
  sahte alarm).
- **v1.1 (V11-303):** Etiket-driven shadow inference + auto retraining —
  4 sınıflı etiketler ML modelinin baseline'ı olur, drift'e göre yeniden
  eğitim.
- **v1.1 (V11-302):** ML personalize liveness eşikleri — preset bazlı
  kural-temelli yerine, tag bazında öğrenilmiş eşikler.

---

**Son güncelleme:** 2026-05-01 — escalation source whitelist eklendi
(yalnız `threshold` ve `cross_sensor` warn → crit yükseltilir).
