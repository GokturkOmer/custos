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
2. Kullanıcı adı ve SSH erişimi ayarla.
3. Sistem güncel olsun: `sudo apt update && sudo apt upgrade -y`.
4. Sabit IP veya DHCP reservation kur (PLC erişimi için).

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

Tarayıcıda:

```
http://custos.local:8000/dashboard
```

veya statik IP ile:

```
http://192.168.X.Y:8000/dashboard
```

Windows istemcilerde `custos.local` çalışmazsa Bonjour servisi gerekli.

---

## 8. Modbus Tag Ekleme

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

## 13. Yedekleme (Opsiyonel)

`/var/custos/backup` dizini setup.sh tarafından hazırlandı. Haftalık
`pg_dump` cron'u aktifleştirmek için:

```bash
cat << 'EOF' | sudo tee /etc/cron.d/custos-backup
# Pazar 03:00 TRT — haftalık Postgres dump
0 3 * * 0 custos pg_dump -h localhost custos | gzip > /var/custos/backup/custos-$(date +\%Y\%m\%d).sql.gz
EOF
sudo chmod 644 /etc/cron.d/custos-backup
```

Retention (60 gün+ sil, opsiyonel):
```bash
find /var/custos/backup -name "*.sql.gz" -mtime +60 -delete
```

**Pilot notu:** Müşteri isterse bu dizini NAS'a rsync'leyebilir. Custos
tarafında cloud sync yok (veri lokal kalır).

---

## 14. Offline Kurulum (internet yok)

1. Hazır bir internetli Ubuntu 24.04 makinede tüm paketleri `apt-get download`
   ile indir ve mini PC'ye USB kopya.
2. TimescaleDB `.deb` paketini https://packagecloud.io/timescale/timescaledb
   adresinden indir, USB'den `sudo apt install ./timescaledb-*.deb` ile kur.
3. Python wheel'leri `pip download -d wheels/ -r requirements.txt` + mini PC'de
   `pip install --no-index --find-links wheels/ -r requirements.txt`.

(Offline pilot gerekirse 3-gün önceden hazırlık yapılmalı.)

---

## 15. Rollback (Kurulum Yarıda Kalırsa)

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
