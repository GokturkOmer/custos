#!/usr/bin/env bash
# Custos Mini PC Kurulum Script'i
# Ubuntu 22.04+ / Debian 12+ icin tasarlanmistir.
#
# Kullanim:
#   sudo bash deploy/setup.sh
#
set -euo pipefail

CUSTOS_USER="custos"
INSTALL_DIR="/opt/custos"
PYTHON_VERSION="3.12"

echo "=== Custos Kurulum Script'i ==="
echo ""

# --- 1. Root kontrolu ---
if [[ $EUID -ne 0 ]]; then
    echo "HATA: Bu script root olarak calistirilmali (sudo bash setup.sh)"
    exit 1
fi

# --- 2. Sistem bagimlilaklari ---
echo "[1/8] Sistem paketleri kuruluyor..."
apt-get update -qq
apt-get install -y -qq \
    python${PYTHON_VERSION} python${PYTHON_VERSION}-venv python${PYTHON_VERSION}-dev \
    postgresql postgresql-client \
    avahi-daemon avahi-utils \
    curl git

# --- 3. TimescaleDB (opsiyonel, yoksa duz PostgreSQL) ---
echo "[2/8] TimescaleDB kontrol ediliyor..."
if ! dpkg -l | grep -q timescaledb; then
    echo "  TimescaleDB bulunamadi — duz PostgreSQL ile devam ediliyor."
    echo "  TimescaleDB icin: https://docs.timescale.com/install/latest/"
fi

# --- 4. Custos kullanicisi ---
echo "[3/8] Custos kullanicisi olusturuluyor..."
if ! id "$CUSTOS_USER" &>/dev/null; then
    useradd --system --create-home --shell /bin/bash "$CUSTOS_USER"
    echo "  Kullanici '$CUSTOS_USER' olusturuldu."
else
    echo "  Kullanici '$CUSTOS_USER' zaten mevcut."
fi

# --- 5. Kurulum dizini ---
echo "[4/8] Kurulum dizini hazirlaniyor..."
mkdir -p "$INSTALL_DIR"
if [[ -d "$(dirname "$0")/../src" ]]; then
    cp -r "$(dirname "$0")/../"* "$INSTALL_DIR/"
fi
chown -R "$CUSTOS_USER:$CUSTOS_USER" "$INSTALL_DIR"

# Parquet arşiv dizini (F11 Paket E) — custos yazabilir, grup okuyabilir.
ARCHIVE_DIR="/var/custos/archive"
mkdir -p "$ARCHIVE_DIR"
chown "$CUSTOS_USER:$CUSTOS_USER" "$ARCHIVE_DIR"
chmod 750 "$ARCHIVE_DIR"

# --- 6. Python venv + pip install ---
echo "[5/8] Python sanal ortami olusturuluyor..."
sudo -u "$CUSTOS_USER" python${PYTHON_VERSION} -m venv "$INSTALL_DIR/.venv"
sudo -u "$CUSTOS_USER" "$INSTALL_DIR/.venv/bin/pip" install --upgrade pip -q
sudo -u "$CUSTOS_USER" "$INSTALL_DIR/.venv/bin/pip" install -e "$INSTALL_DIR" -q
echo "  Bagimliklar yuklendi."

# --- 7. Veritabani ---
echo "[6/8] Veritabani olusturuluyor..."
if ! sudo -u postgres psql -lqt | cut -d \| -f 1 | grep -qw custos; then
    sudo -u postgres createuser --no-superuser --no-createdb --no-createrole custos 2>/dev/null || true
    # Varsayilan sifre — kurulumdan sonra .env dosyasindan degistirilmeli!
    sudo -u postgres psql -c "ALTER USER custos WITH PASSWORD 'degistir-bu-bir-ornektir';" 2>/dev/null
    sudo -u postgres createdb --owner=custos custos
    echo "  Veritabani 'custos' olusturuldu."
    echo "  UYARI: Varsayilan DB sifresi kullanildi. .env dosyasindan degistirin!"
else
    echo "  Veritabani 'custos' zaten mevcut."
fi

# .env dosyasi
if [[ ! -f "$INSTALL_DIR/.env" ]]; then
    cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env"
    echo "  .env dosyasi .env.example'dan kopyalandi. Lutfen duzenleyin!"
fi

# Alembic migration
echo "  Migration calistiriliyor..."
cd "$INSTALL_DIR"
sudo -u "$CUSTOS_USER" "$INSTALL_DIR/.venv/bin/alembic" upgrade head

# --- 8. VAPID key uretimi ---
echo "[7/8] VAPID anahtarlari kontrol ediliyor..."
if ! grep -q "CUSTOS_VAPID_PRIVATE_KEY=." "$INSTALL_DIR/.env" 2>/dev/null; then
    echo "  VAPID anahtarlari bulunamadi. Uretmek icin:"
    echo "    sudo -u $CUSTOS_USER $INSTALL_DIR/.venv/bin/python scripts/generate_vapid_keys.py"
    echo "  Ciktiyi .env dosyasina yapistiirin."
fi

# --- 9. Systemd service ---
echo "[8/8] Systemd service kuruluyor..."
cp "$INSTALL_DIR/deploy/custos.service" /etc/systemd/system/custos.service
systemctl daemon-reload
systemctl enable custos.service
echo "  custos.service aktif edildi."

# --- 10. mDNS (avahi) ---
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
echo "  1. .env dosyasini duzenleyin: $INSTALL_DIR/.env"
echo "  2. VAPID anahtarlarini uretin (yukaridaki komut)"
echo "  3. Servisi baslatin: sudo systemctl start custos"
echo "  4. Durumu kontrol edin: sudo systemctl status custos"
echo "  5. Tarayicide acin: http://custos.local:8000/dashboard"
echo ""
