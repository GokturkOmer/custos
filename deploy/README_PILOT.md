# Custos Pilot Kurulum Rehberi

Pilot müşteriye teslim edilen mini PC'nin **sıfırdan kurulum** prosedürü.
Hedef: saha teknisyeni bu belgeyi takip ederek 1 saat içinde kurulumu tamamlasın
(2. tur sıfırdan kurulum 30 dakikanın altında).

---

## 1. Donanım Gereksinimleri

| Birim | Minimum | Önerilen (Pilot) |
|-------|---------|------------------|
| CPU | Intel N100 (4 çekirdek) | Intel N200 fansız |
| RAM | 8 GB | **16 GB** |
| Disk | 500 GB NVMe SSD | **2 TB NVMe SSD** |
| Ağ | Gigabit Ethernet (PLC'lere) | — |
| OS | Ubuntu 24.04 LTS | — |

Mini PC örnekleri: Beelink EQ14 (N200), MeLE Quieter 4C, Intel NUC 13 Pro.

**2 TB SSD neden zorunlu:** 365 gün ham veri (~90 GB / 200 tag) + 3 yıl dakika
aggregate (~1 GB) + Parquet aylık arşiv (~10 GB/yıl) + OS ve buffer.

---

## 2. OS Kurulumu

1. Ubuntu Server 24.04 LTS minimal kurulum (USB'den).
2. **LUKS disk şifrelemesi** — kurulum sırasında "Encrypt the new
   Ubuntu installation" seçeneğini etkinleştir (bkz. §2.1 LUKS Runbook).
3. Kullanıcı adı ve SSH erişimi ayarla (sadece **anahtarla giriş**;
   `setup.sh` parola ve root login'i kapatır — V11-112).
4. Sistem güncel olsun: `sudo apt update && sudo apt upgrade -y`.
5. Sabit IP veya DHCP reservation kur (PLC erişimi için).
6. `timedatectl status` çıktısında `NTP service: active`,
   `System clock synchronized: yes` görünmeli (V11-113). `setup.sh`
   bu kontrolü otomatik yapar; manuel doğrulamak için yeterli.

### 2.1 LUKS Runbook (V11-112)

LUKS, mini PC fiziksel olarak çalındığında diskin offline okunmasını
engeller. Pilot kurulum şartı; **OS kurulum aşamasında** etkinleştirilir
— sonradan eklemek ancak diski yeniden formatlayarak mümkün.

#### Kurulum sırasında

1. Ubuntu installer "Storage configuration" ekranında
   **"Encrypt the new Ubuntu installation for security"** kutusunu işaretle.
2. **Recovery / passphrase** belirle:
   - **Boot şifresi (passphrase)**: Mini PC her açıldığında istenir.
     - **Müşteri tutar**: Kasa kapalıysa (saha yetkilisi açar) → operasyon
       basit, ama PC restart sırasında müşteri yardımı gerekir.
     - **Geliştirici tutar (önerilen pilot)**: VPN üzerinden uzaktan
       müdahale + saha SSH ile çözüm. Müşteriye fiziksel kasa anahtarı
       teslim edilir; LUKS şifresi geliştiricide kalır.
3. **Recovery key** üret (LUKS otomatik teklif eder veya
   `cryptsetup luksAddKey` ile sonradan eklenir):
   - Yazıcı çıktısı + USB metin dosyası — **dışarıda kasada** sakla.
   - Recovery key kaybolursa disk açılamaz. Yedek bir kopya geliştirici
     ofisinde saklanır.

#### Doğrulama (kurulum sonrası)

```bash
# Açılan disk LUKS-mapped mı kontrol et
sudo lsblk -o NAME,FSTYPE,MOUNTPOINTS

# Beklenen: nvme0n1p3 (veya benzeri) altında "crypto_LUKS" + onun altında
# ext4 mount /. Eğer nvme0n1p3 doğrudan ext4 ise LUKS yok — yeniden kur.

sudo cryptsetup status <crypt_dev>
# "type: LUKS2", "cipher: aes-xts-plain64" görünmeli.
```

#### Recovery key ekleme (sonradan)

```bash
# Mevcut passphrase ile yeni anahtar slot ekle
sudo cryptsetup luksAddKey /dev/nvme0n1p3
# Mevcut şifre sorulur, ardından yeni recovery passphrase iki kez
sudo cryptsetup luksDump /dev/nvme0n1p3 | grep "Key Slot"
# Slot 0: ENABLED (boot şifresi), Slot 1: ENABLED (recovery)
```

**Uyarı:** LUKS aktivasyonu **sadece OS kurulumunda** yapılır. Mevcut
sistem üzerinde root partition'ı şifrelemek için disk tam yedeği +
yeniden kurulum gerekir. Pilot kurulumdan sonra bu adım atlanmamalıdır.

---

## 3. Proje Kopyalama

USB üzerinden veya (internet varsa) git clone ile:

```bash
# Opsiyon A: USB
sudo cp -r /media/usb/custos /tmp/custos

# Opsiyon B: Git (internet gerekli)
cd /tmp && git clone https://github.com/<org>/custos.git
```

---

## 4. Tek Komut Kurulum

```bash
cd /tmp/custos
sudo bash deploy/setup.sh
```

Script otomatik olarak:

- [1/10] Sistem önkoşulları (Ubuntu versiyon, RAM ≥ 2 GB, disk ≥ 8 GB) kontrol
- [2/10] Sistem paketleri (Python 3.12, PostgreSQL 16, avahi, curl, git, openssl)
- [3/10] **TimescaleDB** 2.x PGDG + Packagecloud repo'dan otomatik kurulur (zorunlu)
- [4/10] `custos` sistem kullanıcısı
- [5/10] `/opt/custos`, `/var/custos/archive`, `/var/custos/backup`, `/var/log/custos` dizinleri
- [6/10] Python venv + `pip install -e .`
- [7/10] PostgreSQL DB + **rastgele güçlü DB şifresi** (`openssl rand -base64 32`) → `.env` otomatik dolu
- [8/10] **Seed**: `scripts/seed_asset_templates.py` (9 AVM Template Pack) + `seed_maintenance_checklists.py`
- [9/10] **VAPID anahtarı otomatik üretim** → `.env`'e idempotent yazılır
- [10/10] `custos.service` + `custos-critical.service` systemd enable; mDNS avahi

**Çıktı kodları:**
- 0: Başarı
- 1: Genel hata
- 2: Önkoşul eksik (Ubuntu eski, RAM/disk yetersiz)
- 3: DB hatası (TimescaleDB kurulum veya migration)
- 4: Permission (root değil)

Kurulum süresi: **10–15 dakika** (internet hızına bağlı).

---

## 5. `.env` Kontrolü

Kurulum sonrası `.env` çoğunlukla dolu — sadece opsiyonel ayarları gözden geçir:

```bash
sudo cat /opt/custos/.env
```

Anahtarlar:
- `POSTGRES_PASSWORD` — setup.sh tarafından rastgele üretildi (değiştirme gereksiz).
- `CUSTOS_VAPID_*` — otomatik üretildi.
- `CUSTOS_TIMEZONE` — default `Europe/Istanbul` (AVM için doğru).
- `CUSTOS_HOST_IP` — TLS sertifikası bu IP için üretildi (V11-102 / P-03).
  IT bir statik IP atadı ise burada doğrula; değiştirilirse
  `sudo bash /opt/custos/scripts/generate_tls_cert.sh --force` ile cert yenilenmeli
  ve `sudo systemctl reload caddy` çağrılmalı.
- `LOG_LEVEL` — default `INFO`; sorun araştırırken `DEBUG` yapılabilir.

**Önemli:** `.env` sahiplik: `custos:custos`, mod `0600`. Başka kullanıcıya
okutma.

---

## 6. Servisleri Başlat

```bash
sudo systemctl start custos.service custos-critical.service
sudo systemctl status custos.service custos-critical.service
```

Beklenen: her iki servis de `active (running)`.

- `custos.service` — analytics + dashboard + archive + KPI + anomaly + bakım + disk telemetri (tek süreç, port 8000).
- `custos-critical.service` — Modbus collector (ayrı süreç, daha az kritik olmayan yüklerden izole).

---

## 7. Dashboard Erişimi

Tarayıcıda **HTTPS** ile:

```
https://192.168.X.Y/login
```

(`X.Y` = `.env` içindeki `CUSTOS_HOST_IP`)

HTTP üzerinden gelen istekler 301 ile HTTPS'e yönlendirilir; cookie sadece
HTTPS üzerinden gönderilir (Secure flag, V11-102).

### 7.1 İlk girişte tarayıcı uyarısı (TOFU)

Self-signed sertifika kullanıldığı için tarayıcı **ilk girişte** uyarı
gösterir:

> **Bu bağlantı güvenli değil**
> NET::ERR_CERT_AUTHORITY_INVALID

Bu **beklenen** davranıştır — gerçek bir güvenlik açığı değildir. Sertifika
mini PC üzerinde lokal olarak üretildi ve trafiği şifreliyor; yalnızca bir
bilinmeyen yetkili (CA) tarafından imzalanmadığı için tarayıcı tanımıyor.

**Yapılacak:**
1. **Gelişmiş** (Chromium) / **Riski Kabul Et ve Devam Et** (Firefox) tıkla.
2. Sayfanın yüklenmesini bekle. Üst köşede **kilitli kilit** ikonu (Firefox)
   veya **uyarı simgesi** (Chrome) görünür — bu cihazda kayıtlı.
3. Sonraki girişlerde uyarı tekrar gelmez (TOFU — Trust On First Use).

### 7.2 Sertifika yenileme

Cert 10 yıl geçerli; normalde dokunulmaz. Mini PC IP'si değiştiğinde:

```bash
# 1. .env'deki CUSTOS_HOST_IP'yi güncelle
sudo nano /opt/custos/.env

# 2. Cert'i yeniden üret
sudo CUSTOS_HOST_IP=<yeni-IP> bash /opt/custos/scripts/generate_tls_cert.sh --force

# 3. Caddyfile'ı yeniden render et + reload
sudo sed "s|\${CUSTOS_HOST_IP}|<yeni-IP>|g" /opt/custos/deploy/Caddyfile.template \
    | sudo tee /etc/caddy/Caddyfile >/dev/null
sudo systemctl reload caddy
```

Tarayıcıda eski IP için kayıtlı uyarı kalmaması için tarayıcı önbelleğini
temizlemek gerekebilir (`chrome://net-internals/#hsts`).

---

## 8. Modbus Tag Ekleme

### 8.0 Ağ Topolojisi (Önkoşul — Güvenlik)

**Önemli:** Modbus TCP protokolü authentication desteklemez (sektör
standardı). PLC'lere yetkisiz erişimi engellemek için aşağıdaki ağ
topolojisi şarttır:

- PLC'leri **ayrı bir VLAN/subnet'te** tutun (örn. `192.168.10.0/24`).
- Custos mini PC'sini bu VLAN'a yönlendirin; router/switch ACL veya
  firewall kuralı ile **diğer cihazların PLC trafiğini engelleyin**
  (sadece Custos IP'sinden TCP/502 izinli).
- Custos mini PC'nin kendi yönetim arayüzü (8001/HTTPS) ise tesis
  LAN'ında veya yalnız operator'ün VLAN'ında erişilebilir olmalı.

Tipik akış: `Plant LAN ↔ Custos mini PC ↔ PLC VLAN`.

Custos mimari olarak **yalnızca okur**; Modbus write fonksiyonları
kodda implement edilmemiştir (bkz. CLAUDE.md kuralı). Yine de bu ağ
izolasyonu defansif derinliği sağlar — saldırgan başka bir cihazdan
PLC'lere doğrudan komut gönderemez.

### 8a. Dashboard'dan otomatik keşif (önerilen)

1. Dashboard → **Settings → Connection Profiles** → "Yeni profil" (PLC IP, port 502, unit ID).
2. **Settings → Scan** → taramayı başlat → bulunan register'lar `tags` tablosuna eklenir.
3. Her tag için `asset_type` + `role_key` bind: **Settings → Asset Templates** (AVM Template Pack'ten `ahu`, `fcu`, `chiller`, vb. seç).

### 8b. YAML şablon re-seed (opsiyonel)

Yeni şablon eklendiğinde veya güncelleme sonrası:

```bash
sudo -u custos /opt/custos/.venv/bin/python /opt/custos/scripts/seed_asset_templates.py
```

Idempotent — `slug` UNIQUE, `upsert` ile mevcut bağlamalar korunur.

---

## 9. Alarm Testi

1. Dashboard → **Alarms → New Threshold** (örn: `ahu_supply_temp > 28 °C`).
2. PLC tarafında register'ı tetikle (simülatör veya gerçek set-point değişimi).
3. 2-3 polling cycle içinde alarm geldi mi **Alarms → Active** bak.
4. **Web Push** test: dashboard → bell ikonu → "Test bildirimi" (VAPID + service worker aktif olmalı).

---

## 10. Günlük Sağlık Kontrolü

```bash
sudo -u custos /opt/custos/.venv/bin/python /opt/custos/scripts/healthcheck.py --json
```

6 kontrol (hepsi OK ise exit 0):

1. `db_connect` — PostgreSQL erişim
2. `timescaledb_extension` — TimescaleDB yüklü
3. `alembic_current_head` — Migration güncel
4. `dashboard_http` — `/dashboard/overview` 200 dönüyor
5. `vapid_keys_present` — Web Push anahtarları dolu
6. `disk_free` — `/var/custos` için %15+ boş

**Monitoring entegrasyonu:** `--json` çıktısını cron veya başka bir monitor
pipeline'a yönlendir.

---

## 11. Sorun Giderme

### 11.1 Dashboard açılmıyor
```bash
sudo systemctl status custos.service
sudo journalctl -u custos.service -n 100 --no-pager
```

### 11.2 Modbus toplama yok (canlı değerler gelmiyor)
```bash
sudo systemctl status custos-critical.service
sudo journalctl -u custos-critical.service -n 100 --no-pager
```
Çıkmışsa: `sudo systemctl restart custos-critical.service`.

### 11.3 PLC erişim yok
```bash
# Port erişimi
nc -zv PLC_IP 502
# Firewall
sudo ufw status
```
Aynı ağ segmentinde olduğundan emin ol.

### 11.4 Veritabanı bağlantı yok
```bash
sudo systemctl status postgresql
sudo -u custos PGPASSWORD=$(grep POSTGRES_PASSWORD /opt/custos/.env | cut -d= -f2) \
    psql -h localhost -U custos -d custos -c "SELECT 1"
```

### 11.5 TimescaleDB extension yok
```bash
sudo -u postgres psql -d custos -c "SELECT extversion FROM pg_extension WHERE extname='timescaledb';"
# Yoksa:
sudo -u postgres psql -d custos -c "CREATE EXTENSION IF NOT EXISTS timescaledb;"
```

### 11.6 Migration versiyonu eski
```bash
cd /opt/custos && sudo -u custos /opt/custos/.venv/bin/alembic current
sudo -u custos /opt/custos/.venv/bin/alembic upgrade head
```

### 11.7 VAPID anahtarı boş
```bash
sudo -u custos /opt/custos/.venv/bin/python /opt/custos/scripts/generate_vapid_keys.py \
    --write-env /opt/custos/.env
sudo systemctl restart custos.service
```

### 11.8 mDNS (`custos.local`) çalışmıyor
```bash
sudo systemctl status avahi-daemon
avahi-browse -a -t | grep Custos
```
Windows istemcilerde Bonjour gerekli.

### 11.9 Push bildirimi gelmiyor
- `.env` → `CUSTOS_VAPID_*` dolu mu
- Dashboard → "Abone Ol" tıklanmış mı
- Tarayıcı bildirim izni verilmiş mi
- HTTP üzerinden Web Push LAN'da çalışır (service worker localhost/LAN'da OK)

### 11.10 Disk %85+
Dashboard → **Settings → Veri Saklama** → retention daralt (365 → 180 gün).
Parquet arşiv `/var/custos/archive` dışarı aktarılabilir (NAS'a kopya).

---

## 12. Uzaktan Destek (VPN)

Pilot sözleşmesine göre **müşteri IT (Torunlar IT)** VPN kurar. Kurulduktan
sonra:

```bash
# Müşteri VPN tünelinden:
ssh custos@<mini_pc_lan_ip>
```

**Kurallar:**
- Veri aktarımı **YASAK** — sadece teşhis/bakım.
- Geliştirici komutları `/opt/custos/` altında çalışır, sistem dosyalarına
  müdahale gerektiren bir şey olursa müşteri IT ile beraber.
- VPN bağlantı log'u müşteri tarafında takip edilir.

---

## 13. Yedekleme + Restore (Opsiyonel)

### Yedekleme

`/var/custos/backup` dizini setup.sh tarafından hazırlandı. Haftalık
`pg_dump` cron'u aktifleştirmek için (custos_admin ile — owner yetkisi
yedek bütünlüğü için gerekli):

```bash
cat << 'EOF' | sudo tee /etc/cron.d/custos-backup
# Pazar 03:00 TRT — haftalık Postgres dump (custos_admin ile)
0 3 * * 0 custos /opt/custos/.venv/bin/python -c "from custos.shared.config import settings; import sys; sys.stdout.write(settings.database_admin_url)" | xargs -I {} pg_dump --no-owner --no-acl -d {} | gzip > /var/custos/backup/custos-$(date +\%Y\%m\%d).sql.gz
EOF
sudo chmod 644 /etc/cron.d/custos-backup
```

Retention (60 gün+ sil, opsiyonel):
```bash
find /var/custos/backup -name "*.sql.gz" -mtime +60 -delete
```

### Restore (V11-106 user ayrımı)

DB user ayrımı sayesinde restore **custos_admin** ile yapılır (DDL +
ownership yetkisi gerekli). custos_app ile restore başarısız olur:
runtime user'ın CREATE TABLE / GRANT yetkisi yoktur (tasarımın bir
parçası — credential sızsa restore-tabanlı kötü amaç da engellenir).

```bash
# 1. Servisleri durdur
sudo systemctl stop custos-critical.service custos.service

# 2. .env'den admin DSN'i oku
ADMIN_DSN=$(grep -E '^CUSTOS_DB_ADMIN_DSN=' /opt/custos/.env | cut -d= -f2-)

# 3. DB'yi sil ve yeniden yarat (admin owner)
sudo -u postgres dropdb custos
sudo -u postgres createdb --owner=custos_admin custos

# 4. Restore (custos_admin ile)
gunzip -c /var/custos/backup/custos-YYYYMMDD.sql.gz | psql "$ADMIN_DSN"

# 5. custos_app yetkilerini yeniden ver (drop ettiğin için kayboldular)
sudo -u postgres psql -d custos <<SQL
GRANT CONNECT ON DATABASE custos TO custos_app;
GRANT USAGE ON SCHEMA public TO custos_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO custos_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO custos_app;
ALTER DEFAULT PRIVILEGES FOR ROLE custos_admin IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO custos_app;
ALTER DEFAULT PRIVILEGES FOR ROLE custos_admin IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO custos_app;
SQL

# 6. Servisleri yeniden başlat
sudo systemctl start custos.service custos-critical.service
```

**Pilot notu:** Müşteri isterse bu dizini NAS'a rsync'leyebilir. Custos
tarafında cloud sync yok (veri lokal kalır).

---

## 14. Sistem Güncellemeleri (Git Pull)

Custos kodu güncellendiğinde mini PC'ye yeni sürümü çekmek için. Repo
private olduğu için Personal Access Token (PAT) gerekli — ilk kurulumda
bir kere ayarla, sonra `git pull` parolasız çalışır.

### 14.1 PAT (Personal Access Token) oluşturma

1. GitHub'da **Settings → Developer settings → Personal access tokens
   (classic) → Generate new token (classic)**.
2. **Note**: `custos-pilot-mini-pc` (hangi cihaz olduğunu hatırlamak için).
3. **Expiration**: 90 gün (kısa tutulması güvenlik için iyi; süresi dolunca
   yenilenir — bkz. §14.4).
4. **Yetkiler**: yalnızca `repo` (private repo erişimi). Diğer kutulara
   dokunma — least-privilege.
5. **Generate token** → token'ı (`ghp_<TOKEN_BURAYA>`, ~40 karakter)
   kopyala. Bu sayfayı kapatınca bir daha gösterilmez; geliştirici
   ofisindeki şifre kasasına da bir kopya kaydet.

### 14.2 Mini PC'de tek seferlik PAT kurulumu

Pilot kurulumdan hemen sonra (veya PAT yenilendikten sonra) tek sefer
çalıştırılır. `<PAT>` yerine üstteki token'ı, `<org>` yerine GitHub
organizasyonunu/kullanıcısını yaz:

```bash
# Remote URL'sine PAT'ı göm — sadece custos kullanıcısının okuyabileceği
# .git/config dosyasına yazılır.
sudo -u custos git -C /opt/custos remote set-url origin \
    https://<PAT>@github.com/<org>/custos.git

# Test: ilk fetch parolasız çalışmalı
sudo -u custos git -C /opt/custos fetch
```

**Güvenlik notu:** PAT URL'in içine yazıldığı için `/opt/custos/.git/config`
dosyası `custos:custos` sahipliğinde + `0640` izinde tutulmalıdır
(setup.sh varsayılanı zaten bu). Başka kullanıcıya okutma.

### 14.3 Güncelleme akışı

Yeni bir sürüm tag'i çıktığında (örn. `v1.1.2`):

```bash
# 1. Servisleri durdur (collector + dashboard)
sudo systemctl stop custos.service custos-critical.service

# 2. Yeni kodu çek + tag'e geç
sudo -u custos git -C /opt/custos fetch --tags
sudo -u custos git -C /opt/custos checkout v1.1.2

# 3. Bağımlılıkları güncelle (yeni paket eklenmişse)
sudo -u custos /opt/custos/.venv/bin/pip install -e /opt/custos -q

# 4. DB migration (yeni alembic versiyonu varsa)
cd /opt/custos
sudo -u custos /opt/custos/.venv/bin/alembic upgrade head

# 5. Servisleri başlat
sudo systemctl start custos-critical.service custos.service

# 6. Sağlık kontrolü
sudo -u custos /opt/custos/.venv/bin/python /opt/custos/scripts/healthcheck.py --json
```

**Rollback:** Yeni sürümde sorun çıkarsa `git checkout <eski-tag>` +
`alembic downgrade` (release notes'ta downgrade hash'i belirtilir) +
servisleri yeniden başlat.

### 14.4 PAT yenileme

Token'ın süresi dolmadan **1 hafta önce** yenilemeyi planla:

1. Geliştirici takvimine "Custos PAT yenile" hatırlatıcısı (90 gün
   sonrasının 1 hafta öncesi).
2. GitHub'da yeni token üret (§14.1).
3. Mini PC'de §14.2 komutunu yeni token'la tekrar çalıştır — eski URL
   üzerine yazılır.
4. Eski token'ı GitHub'da **Revoke** et (kullanılmıyorsa).

Süre dolduktan sonra `git fetch` "authentication failed" verirse panik
yapma — yenile, yeniden ayarla, devam et.

---

## 15. Offline Kurulum (internet yok)

1. Hazır bir internetli Ubuntu 24.04 makinede tüm paketleri `apt-get download`
   ile indir ve mini PC'ye USB kopya.
2. TimescaleDB `.deb` paketini https://packagecloud.io/timescale/timescaledb
   adresinden indir, USB'den `sudo apt install ./timescaledb-*.deb` ile kur.
3. Python wheel'leri `pip download -d wheels/ -r requirements.txt` + mini PC'de
   `pip install --no-index --find-links wheels/ -r requirements.txt`.

(Offline pilot gerekirse 3-gün önceden hazırlık yapılmalı.)

---

## 16. Rollback (Kurulum Yarıda Kalırsa)

```bash
sudo systemctl disable --now custos.service custos-critical.service 2>/dev/null
sudo rm -f /etc/systemd/system/custos{,-critical}.service
sudo systemctl daemon-reload
sudo -u postgres dropdb custos 2>/dev/null
sudo -u postgres dropuser custos 2>/dev/null
sudo rm -rf /opt/custos /var/custos /var/log/custos
sudo userdel -r custos 2>/dev/null
sudo rm -f /etc/logrotate.d/custos /etc/avahi/services/custos.service /etc/cron.d/custos-backup
```

Sonra `setup.sh` tekrar çalıştırılabilir (temiz kurulum).

---

## Destek

- Geliştirici iletişim: (pilot sözleşmesinde belirtilir)
- Kritik sorun: VPN üzerinden SSH + `journalctl` dökümü
- Saha raporlama: haftalık healthcheck JSON + dashboard ekran görüntüsü
