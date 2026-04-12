# Custos Pilot Kurulum Rehberi

## Donanim Gereksinimleri

| Birim | Minimum | Onerilen |
|-------|---------|----------|
| CPU | 2 cekirdek (x86_64/ARM64) | 4 cekirdek |
| RAM | 2 GB | 4 GB |
| Disk | 32 GB SSD | 64 GB SSD |
| Ag | Ethernet (Modbus PLC'lere erisim) | — |
| OS | Ubuntu 22.04 LTS / Debian 12 | Ubuntu 24.04 LTS |

Mini PC onerileri: Intel NUC, Beelink, MeLE Quieter

## Adim Adim Kurulum

### 1. OS Kurulumu

Ubuntu Server 22.04+ veya Debian 12+ minimal kurulum yapin.
Kullanici adiniz ve SSH erisimi ayarlayin.

### 2. Proje Dosyalarini Kopyalayin

```bash
# USB veya scp ile
scp -r custos/ kullanici@minipc:/tmp/custos
```

### 3. Kurulum Script'ini Calistirin

```bash
cd /tmp/custos
sudo bash deploy/setup.sh
```

Script otomatik olarak:
- Python 3.12, PostgreSQL, avahi kurar
- `custos` kullanicisi ve `/opt/custos` dizini olusturur
- Python venv ve bagimoliklari kurar
- Veritabani olusturur ve migration calistirir
- Systemd service kaydeder

### 4. VAPID Anahtarlarini Uretin

```bash
sudo -u custos /opt/custos/.venv/bin/python /opt/custos/scripts/generate_vapid_keys.py
```

Ciktiyi `/opt/custos/.env` dosyasina yapistirir.

### 5. Baglanti Ayarlari

`/opt/custos/.env` dosyasini duzenleyin:
- Veritabani sifresi
- VAPID anahtarlari
- Log seviyesi

### 6. Servisi Baslatin

```bash
sudo systemctl start custos
sudo systemctl status custos
```

### 7. Tarayicida Acin

```
http://custos.local:8000/dashboard
```

veya Mini PC'nin IP adresi ile:

```
http://192.168.1.XXX:8000/dashboard
```

## Ilk Calistirma Kontrol Listesi

- [ ] Dashboard aciliyor (`/dashboard/overview`)
- [ ] Settings sayfasinda "Veritabani: Bagli" gorunuyor
- [ ] Connection profile eklenebiliyor
- [ ] Modbus taramasi calistirilabiliyor
- [ ] Tag'ler otomatik kesfediliyor
- [ ] Canli degerler gorunuyor (`/dashboard/sensors`)
- [ ] Threshold tanimlanabiliyor
- [ ] Alarm tetiklenince bildirim geliyor (VAPID varsa)
- [ ] Push bildirim test butonu calisiyor

## Troubleshooting

### Servis baslamiyor

```bash
sudo journalctl -u custos -n 50 --no-pager
```

### Veritabani baglantisi yok

```bash
sudo systemctl status postgresql
sudo -u custos psql -h localhost -U custos -d custos -c "SELECT 1"
```

### Modbus baglantisi kurulamiyor

- PLC ile ayni ag segmentinde oldugunuzdan emin olun
- Firewall kurallari kontrol edin: `sudo ufw status`
- Modbus port'u (varsayilan 502) acik mi: `nc -zv PLC_IP 502`

### Push bildirimler gelmiyor

- VAPID anahtarlarinin .env dosyasinda tanimli oldugundan emin olun
- Settings sayfasinda "Abone Ol" butonuna tiklanmis mi kontrol edin
- Tarayici bildirim izni verilmis mi kontrol edin
- HTTP uzerinden push bildirimi sadece localhost'ta calisir
  (LAN'da HTTP yeterlidir cunku Service Worker localhost'ta calisir)

### mDNS (custos.local) calismiyor

```bash
sudo systemctl status avahi-daemon
avahi-browse -a
```

Windows istemcilerde Bonjour veya mDNS destegi gerekebilir.
