#!/usr/bin/env bash
# Custos Mini PC Kurulum Script'i
# Ubuntu 22.04+ / 24.04 LTS icin tasarlanmistir (pilot: Ubuntu 24.04 LTS).
#
# Kullanim:
#   sudo bash deploy/setup.sh
#
# Exit kodlari:
#   0 — Basari
#   1 — Genel hata (set -e ile implicit)
#   2 — Onkosul eksik (Ubuntu versiyon/RAM/disk yetersiz)
#   3 — Veritabani hatasi (TimescaleDB kurulum/migration)
#   4 — Permission hatasi (root degil)
#
set -euo pipefail

CUSTOS_USER="custos"
INSTALL_DIR="/opt/custos"
ARCHIVE_DIR="/var/custos/archive"
BACKUP_DIR="/var/custos/backup"
LOG_DIR="/var/log/custos"
PYTHON_VERSION="3.12"
MIN_RAM_GB=2
REC_RAM_GB=8
MIN_DISK_GB=8

echo "=== Custos Kurulum Script'i ==="
echo ""

# --- 1. Root kontrolu ---
if [[ $EUID -ne 0 ]]; then
    echo "HATA: Bu script root olarak calistirilmali (sudo bash deploy/setup.sh)" >&2
    exit 4
fi

# --- 2. Pre-flight sistem kontrolleri ---
echo "[1/10] Sistem onkosullari kontrol ediliyor..."

# Ubuntu versiyon — 22.04+ zorunlu, 24.04 onerilen
if ! command -v lsb_release >/dev/null 2>&1; then
    apt-get update -qq && apt-get install -y -qq lsb-release
fi
DISTRO_ID=$(lsb_release -is 2>/dev/null || echo "unknown")
DISTRO_VER=$(lsb_release -rs 2>/dev/null || echo "0")
DISTRO_CODENAME=$(lsb_release -cs 2>/dev/null || echo "unknown")
case "$DISTRO_ID" in
    Ubuntu)
        MAJOR=${DISTRO_VER%%.*}
        if (( MAJOR < 22 )); then
            echo "HATA: Ubuntu $DISTRO_VER desteklenmiyor — minimum 22.04 gerekli." >&2
            exit 2
        fi
        if (( MAJOR < 24 )); then
            echo "  UYARI: Pilot icin Ubuntu 24.04 LTS onerilir, $DISTRO_VER ile devam."
        else
            echo "  Ubuntu $DISTRO_VER — OK."
        fi
        ;;
    Debian)
        echo "  UYARI: Debian destekleniyor ama test edilmemis. Dikkatli ilerle."
        ;;
    *)
        echo "HATA: Desteklenmeyen dagitim: $DISTRO_ID $DISTRO_VER" >&2
        exit 2
        ;;
esac

# RAM kontrolu
TOTAL_RAM_GB=$(awk '/^MemTotal:/ {printf "%.0f", $2/1024/1024}' /proc/meminfo)
if (( TOTAL_RAM_GB < MIN_RAM_GB )); then
    echo "HATA: Yetersiz RAM: ${TOTAL_RAM_GB} GB (minimum ${MIN_RAM_GB} GB)" >&2
    exit 2
fi
if (( TOTAL_RAM_GB < REC_RAM_GB )); then
    echo "  UYARI: Pilot icin onerilen RAM 16 GB, mevcut ${TOTAL_RAM_GB} GB."
else
    echo "  RAM: ${TOTAL_RAM_GB} GB — OK."
fi

# /opt mount icin bos disk (root fs'ye fallback)
OPT_BASE=$(df -BG --output=target /opt 2>/dev/null | awk 'NR==2 {print $1}' || echo "/")
AVAIL_GB=$(df -BG --output=avail "$OPT_BASE" | awk 'NR==2 {sub("G",""); print $1}')
if (( AVAIL_GB < MIN_DISK_GB )); then
    echo "HATA: Yetersiz bos disk: ${AVAIL_GB} GB (minimum ${MIN_DISK_GB} GB)" >&2
    exit 2
fi
echo "  Disk bos: ${AVAIL_GB} GB — OK."

# --- 3. Sistem paketleri ---
echo "[2/10] Sistem paketleri kuruluyor..."
apt-get update -qq
apt-get install -y -qq \
    python${PYTHON_VERSION} python${PYTHON_VERSION}-venv python${PYTHON_VERSION}-dev \
    postgresql postgresql-client \
    avahi-daemon avahi-utils \
    curl git wget gnupg openssl \
    apt-transport-https ca-certificates \
    logrotate cron
echo "  Temel paketler kuruldu."

# --- 4. TimescaleDB — ZORUNLU (F11 hypertable + continuous aggregate) ---
echo "[3/10] TimescaleDB kontrol ediliyor..."
if ! dpkg -l 2>/dev/null | grep -qE 'timescaledb-2-postgresql-(14|15|16)'; then
    echo "  TimescaleDB bulunamadi, kuruluyor..."

    # PostgreSQL Global Development Group (PGDG) repo — pg16 icin
    if [[ ! -f /etc/apt/sources.list.d/pgdg.list ]]; then
        install -d /usr/share/postgresql-common/pgdg
        wget --quiet -O /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
            https://www.postgresql.org/media/keys/ACCC4CF8.asc
        echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt ${DISTRO_CODENAME}-pgdg main" \
            > /etc/apt/sources.list.d/pgdg.list
    fi

    # TimescaleDB repo (packagecloud)
    if [[ ! -f /etc/apt/sources.list.d/timescaledb.list ]]; then
        wget --quiet -O /etc/apt/trusted.gpg.d/timescaledb.asc \
            https://packagecloud.io/timescale/timescaledb/gpgkey
        echo "deb https://packagecloud.io/timescale/timescaledb/ubuntu/ ${DISTRO_CODENAME} main" \
            > /etc/apt/sources.list.d/timescaledb.list
    fi

    apt-get update -qq
    if ! apt-get install -y -qq timescaledb-2-postgresql-16; then
        echo "HATA: TimescaleDB kurulamadi. Cevrimdisi kurulum icin README_PILOT.md'ye bak." >&2
        exit 3
    fi

    # shared_preload_libraries ayari (timescaledb-tune otomatik yapar)
    if command -v timescaledb-tune >/dev/null 2>&1; then
        timescaledb-tune --quiet --yes || true
    fi
    systemctl restart postgresql
    echo "  TimescaleDB kuruldu, PostgreSQL yeniden baslatildi."
else
    echo "  TimescaleDB zaten kurulu."
fi

# --- 5. Custos kullanicisi ---
echo "[4/10] Custos kullanicisi hazirlaniyor..."
if ! id "$CUSTOS_USER" &>/dev/null; then
    useradd --system --create-home --shell /bin/bash "$CUSTOS_USER"
    echo "  Kullanici '$CUSTOS_USER' olusturuldu."
else
    echo "  Kullanici '$CUSTOS_USER' zaten mevcut."
fi

# --- 6. Kurulum dizinleri + veri/log klasorleri ---
echo "[5/10] Kurulum ve veri dizinleri hazirlaniyor..."
mkdir -p "$INSTALL_DIR"
if [[ -d "$(dirname "$0")/../src" ]]; then
    cp -r "$(dirname "$0")/../"* "$INSTALL_DIR/"
fi
chown -R "$CUSTOS_USER:$CUSTOS_USER" "$INSTALL_DIR"

# Parquet arsiv dizini (F11 Paket E)
mkdir -p "$ARCHIVE_DIR"
chown "$CUSTOS_USER:$CUSTOS_USER" "$ARCHIVE_DIR"
chmod 750 "$ARCHIVE_DIR"

# PostgreSQL dump yedek dizini (opsiyonel cron — README'de aktifleme)
mkdir -p "$BACKUP_DIR"
chown "$CUSTOS_USER:$CUSTOS_USER" "$BACKUP_DIR"
chmod 750 "$BACKUP_DIR"

# Uygulama log dizini (file logging placeholder — structlog file handler iin)
mkdir -p "$LOG_DIR"
chown "$CUSTOS_USER:$CUSTOS_USER" "$LOG_DIR"
chmod 750 "$LOG_DIR"

echo "  $INSTALL_DIR, $ARCHIVE_DIR, $BACKUP_DIR, $LOG_DIR hazir."

# --- 7. Python venv + bagimoliklar ---
echo "[6/10] Python sanal ortami kuruluyor..."
if [[ ! -d "$INSTALL_DIR/.venv" ]]; then
    sudo -u "$CUSTOS_USER" python${PYTHON_VERSION} -m venv "$INSTALL_DIR/.venv"
fi
sudo -u "$CUSTOS_USER" "$INSTALL_DIR/.venv/bin/pip" install --upgrade pip -q
# setuptools >=78.1.1 — PYSEC-2025-49 path traversal RCE fix (A3 denetim)
sudo -u "$CUSTOS_USER" "$INSTALL_DIR/.venv/bin/pip" install --upgrade 'setuptools>=78.1.1' -q
sudo -u "$CUSTOS_USER" "$INSTALL_DIR/.venv/bin/pip" install -e "$INSTALL_DIR" -q
echo "  Bagimoliklar yuklendi."

# --- 8. Veritabani + .env ---
echo "[7/10] Veritabani hazirlaniyor..."
DB_EXISTS=0
if sudo -u postgres psql -lqt | cut -d \| -f 1 | grep -qw custos; then
    DB_EXISTS=1
fi

# .env yoksa: kopyala + rastgele DB sifresi uret + postgres user sifresini ayarla.
# Mevcutsa dokunma (idempotent — kullanici sifreyi manuel degistirmis olabilir).
ENV_CREATED=0
if [[ ! -f "$INSTALL_DIR/.env" ]]; then
    cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env"
    # openssl ile 32 byte rastgele + URL-safe normalize (/, +, = kaldir).
    DB_PASS=$(openssl rand -base64 32 | tr -d '\n' | tr -d '=' | tr '/+' '_-')
    # sed delimiteri | — base64 URL-safe ciktida | yok, guvenli.
    sed -i "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=${DB_PASS}|" "$INSTALL_DIR/.env"
    chown "$CUSTOS_USER:$CUSTOS_USER" "$INSTALL_DIR/.env"
    chmod 600 "$INSTALL_DIR/.env"
    ENV_CREATED=1
    echo "  .env olusturuldu, DB sifresi rastgele uretildi (chmod 600)."
else
    # Mevcut .env'den sifreyi oku (postgres user sifresini esleme icin).
    DB_PASS=$(grep -E '^POSTGRES_PASSWORD=' "$INSTALL_DIR/.env" | head -1 | cut -d= -f2-)
    if [[ -z "$DB_PASS" ]]; then
        echo "HATA: .env mevcut ama POSTGRES_PASSWORD bos." >&2
        exit 3
    fi
    echo "  .env zaten mevcut, sifre ondan okundu."
fi

# Postgres kullanici + DB olustur (idempotent)
if (( DB_EXISTS == 0 )); then
    sudo -u postgres createuser --no-superuser --no-createdb --no-createrole custos 2>/dev/null || true
    sudo -u postgres createdb --owner=custos custos
    echo "  Veritabani 'custos' olusturuldu."
fi
# Sifre her durumda senkronize edilir (.env ile postgres arasi tutarlilik)
sudo -u postgres psql -v ON_ERROR_STOP=1 -c "ALTER USER custos WITH PASSWORD '${DB_PASS}';" >/dev/null
if (( ENV_CREATED == 1 )); then
    echo "  PostgreSQL kullanici sifresi .env ile eslesti."
fi

# Alembic migration
echo "  Migration calistiriliyor..."
cd "$INSTALL_DIR"
if ! sudo -u "$CUSTOS_USER" "$INSTALL_DIR/.venv/bin/alembic" upgrade head; then
    echo "HATA: Alembic migration basarisiz." >&2
    exit 3
fi

# --- 9. Seed — AVM Template Pack + bakim checklist'leri ---
echo "[8/10] Seed script'leri calistiriliyor..."
# F9 raporu karari: idempotent upsert, hata durumunda uyari ver ama exit etme
# (sablonlar sonradan dashboard API'sinden de tetiklenebilir).
if ! sudo -u "$CUSTOS_USER" "$INSTALL_DIR/.venv/bin/python" \
     "$INSTALL_DIR/scripts/seed_asset_templates.py"; then
    echo "  UYARI: seed_asset_templates basarisiz — dashboard'dan manuel tetiklenebilir." >&2
fi
if ! sudo -u "$CUSTOS_USER" "$INSTALL_DIR/.venv/bin/python" \
     "$INSTALL_DIR/scripts/seed_maintenance_checklists.py"; then
    echo "  UYARI: seed_maintenance_checklists basarisiz — yoksayildi." >&2
fi

# --- 10. VAPID anahtari (otomatik uret + .env'e yaz) ---
echo "[9/10] VAPID anahtarlari kontrol ediliyor..."
if ! grep -qE '^CUSTOS_VAPID_PRIVATE_KEY=.+' "$INSTALL_DIR/.env"; then
    echo "  VAPID anahtarlari uretiliyor..."
    sudo -u "$CUSTOS_USER" "$INSTALL_DIR/.venv/bin/python" \
        "$INSTALL_DIR/scripts/generate_vapid_keys.py" --write-env "$INSTALL_DIR/.env"
else
    echo "  VAPID anahtarlari zaten .env'de mevcut."
fi

# --- 11. Log rotation (placeholder — structlog file handler iin) ---
if [[ -f "$INSTALL_DIR/deploy/logrotate.custos" ]]; then
    cp "$INSTALL_DIR/deploy/logrotate.custos" /etc/logrotate.d/custos
    chmod 644 /etc/logrotate.d/custos
fi

# --- 12. Systemd service'ler (analytics + critical) ---
echo "[10/10] Systemd service'ler kuruluyor..."
cp "$INSTALL_DIR/deploy/custos.service" /etc/systemd/system/custos.service
if [[ -f "$INSTALL_DIR/deploy/custos-critical.service" ]]; then
    cp "$INSTALL_DIR/deploy/custos-critical.service" /etc/systemd/system/custos-critical.service
fi
systemctl daemon-reload
systemctl enable custos.service >/dev/null
if [[ -f "$INSTALL_DIR/deploy/custos-critical.service" ]]; then
    systemctl enable custos-critical.service >/dev/null
fi
echo "  custos.service + custos-critical.service aktif edildi."

# --- 13. mDNS (avahi) ---
AVAHI_SERVICE="/etc/avahi/services/custos.service"
if [[ ! -f "$AVAHI_SERVICE" ]]; then
    cat > "$AVAHI_SERVICE" <<AVAHI_EOF
<?xml version="1.0" standalone='no'?>
<!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<service-group>
  <name>Custos</name>
  <service>
    <type>_http._tcp</type>
    <port>8000</port>
  </service>
</service-group>
AVAHI_EOF
    systemctl restart avahi-daemon
    echo "  mDNS ayarlandi — custos.local:8000"
fi

echo ""
echo "=== Kurulum tamamlandi ==="
echo ""
echo "Siradaki adimlar:"
echo "  1. .env dosyasini gozden gecir: $INSTALL_DIR/.env (VAPID ve DB sifre otomatik dolu)"
echo "  2. Servisleri baslat: sudo systemctl start custos.service custos-critical.service"
echo "  3. Durum kontrolu: sudo systemctl status custos.service custos-critical.service"
echo "  4. Healthcheck: sudo -u $CUSTOS_USER $INSTALL_DIR/.venv/bin/python $INSTALL_DIR/scripts/healthcheck.py --json"
echo "  5. Tarayicida ac: http://custos.local:8000/dashboard"
echo ""
