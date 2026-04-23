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
#   2 — Onkosul eksik (Ubuntu versiyon/RAM/disk yetersiz veya PG 14 dolu)
#   3 — Veritabani hatasi (TimescaleDB kurulum/migration)
#   4 — Permission hatasi (root degil)
#
set -euo pipefail

# Noninteractive apt — dialog tetiklenmesin (PG cluster upgrade prompt, vs).
# v1.0.1 kalem 9: dry-run'da PG 14->18 upgrade dialog 3 kez cikmisti.
export DEBIAN_FRONTEND=noninteractive
export NEEDRESTART_MODE=a  # needrestart otomatik yeniden baslat (Ubuntu 22.04+)

# PG paketlerinin upgrade dialog'unu preset'le — her durumda "no".
# debconf-set-selections apt-get update'ten once cagrilmali ki paket hazirlik
# fazinda dialog yerine preset deger okunsun.
if command -v debconf-set-selections >/dev/null 2>&1; then
    echo 'postgresql postgresql/pg_upgrade boolean false' | debconf-set-selections
fi

CUSTOS_USER="custos"
INSTALL_DIR="/opt/custos"
ARCHIVE_DIR="/var/custos/archive"
BACKUP_DIR="/var/custos/backup"
LOG_DIR="/var/log/custos"
PYTHON_VERSION="3.12"
PG_VERSION="16"
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
echo "[1/11] Sistem onkosullari kontrol ediliyor..."

# Ubuntu versiyon — 22.04+ zorunlu, 24.04 onerilen
if ! command -v lsb_release >/dev/null 2>&1; then
    apt-get update -qq && apt-get install -y -qq lsb-release
fi
DISTRO_ID=$(lsb_release -is 2>/dev/null || echo "unknown")
DISTRO_VER=$(lsb_release -rs 2>/dev/null || echo "0")
DISTRO_CODENAME=$(lsb_release -cs 2>/dev/null || echo "unknown")
NEED_DEADSNAKES=0
case "$DISTRO_ID" in
    Ubuntu)
        MAJOR=${DISTRO_VER%%.*}
        if (( MAJOR < 22 )); then
            echo "HATA: Ubuntu $DISTRO_VER desteklenmiyor — minimum 22.04 gerekli." >&2
            exit 2
        fi
        if (( MAJOR < 24 )); then
            # 22.04 default python = 3.10; deadsnakes PPA ile 3.12 cekilir (v1.0.1 kalem 1).
            echo "  Ubuntu $DISTRO_VER — deadsnakes PPA Python ${PYTHON_VERSION} icin eklenecek."
            NEED_DEADSNAKES=1
        else
            echo "  Ubuntu $DISTRO_VER — Python ${PYTHON_VERSION} native (OK)."
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

# --- 3. APT repo'lari — deadsnakes PPA (Python 3.12) + PGDG (PG 16) + TimescaleDB ---
# Tum repo eklemeleri TEK apt-get update ile sonlanir, paket kurulumu [3/11]'de
# tek transaction. Bu dry-run'daki "4 kez rerun" sorununu giderir (kalem 13).
echo "[2/11] APT repo'lari hazirlaniyor..."

# Temel repo araclari (sonraki repo eklemeleri icin gerekli)
apt-get update -qq
apt-get install -y -qq \
    curl git wget gnupg \
    apt-transport-https ca-certificates \
    software-properties-common \
    lsb-release

# Ubuntu 22.04 -> deadsnakes PPA (kalem 1). 24.04 native 3.12, atlanir.
if (( NEED_DEADSNAKES == 1 )); then
    if [[ ! -f /etc/apt/sources.list.d/deadsnakes-ubuntu-ppa-${DISTRO_CODENAME}.list ]] \
       && ! ls /etc/apt/sources.list.d/ 2>/dev/null | grep -q deadsnakes; then
        add-apt-repository -y ppa:deadsnakes/ppa >/dev/null
        echo "  deadsnakes PPA eklendi (Python ${PYTHON_VERSION})."
    else
        echo "  deadsnakes PPA zaten mevcut."
    fi
fi

# PostgreSQL Global Development Group (PGDG) repo — PG 16 icin (kalem 10).
if [[ ! -f /etc/apt/sources.list.d/pgdg.list ]]; then
    install -d /usr/share/postgresql-common/pgdg
    wget --quiet -O /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
        https://www.postgresql.org/media/keys/ACCC4CF8.asc
    echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt ${DISTRO_CODENAME}-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list
    echo "  PGDG repo eklendi."
fi

# TimescaleDB repo (packagecloud).
if [[ ! -f /etc/apt/sources.list.d/timescaledb.list ]]; then
    wget --quiet -O /etc/apt/trusted.gpg.d/timescaledb.asc \
        https://packagecloud.io/timescale/timescaledb/gpgkey
    echo "deb https://packagecloud.io/timescale/timescaledb/ubuntu/ ${DISTRO_CODENAME} main" \
        > /etc/apt/sources.list.d/timescaledb.list
    echo "  TimescaleDB repo eklendi."
fi

apt-get update -qq

# --- 4. Sistem paketleri (tek transaction) ---
# postgresql-16 explicit (kalem 8) — meta paket "postgresql" PGDG ile latest
# (PG 18'e) yonleniyordu, cluster upgrade dialog'u tetikliyordu.
# timescaledb-2-postgresql-16 TimescaleDB repo ile ayni transaction'a eklendi.
echo "[3/11] Sistem paketleri kuruluyor (Python ${PYTHON_VERSION} + PG ${PG_VERSION} + TimescaleDB)..."
apt-get install -y -qq \
    python${PYTHON_VERSION} python${PYTHON_VERSION}-venv python${PYTHON_VERSION}-dev \
    postgresql-${PG_VERSION} postgresql-client-${PG_VERSION} \
    timescaledb-2-postgresql-${PG_VERSION} \
    avahi-daemon avahi-utils \
    openssl logrotate cron rsync
echo "  Paketler kuruldu."

# --- 5. PostgreSQL cluster hazirligi (kalem 10) ---
# Ubuntu 22.04 default PG 14/main cluster + PGDG ile kurulan PG 16 cluster
# port cakismasi dry-run'da karisiklik yarattigi icin explicit yonetim:
#   1. PG 14/main mevcut ise: bos (user DB yok) -> sessiz drop;
#      dolu -> uyari + exit 2 (kullanici manuel karar versin, Q2).
#   2. PG 16/main yok veya port 5432 degil -> dropcluster + createcluster.
#   3. timescaledb-tune explicit --pg-config ile preload + restart.
echo "[4/11] PostgreSQL ${PG_VERSION} cluster hazirlaniyor..."

if command -v pg_lsclusters >/dev/null 2>&1; then
    # PG 14/main boslugu (kalem 10, Q2): user DB sayisi (postgres/template0/template1 haric)
    if pg_lsclusters -h 2>/dev/null | awk '{print $1"/"$2}' | grep -qx '14/main'; then
        # PG 14/main cluster calisiyorsa psql ile sorgula, degilse baslatip sor,
        # ama bu riskli; pratik yol: cluster bilgisinden data dizinine bakip
        # TEK-SEFER baslat, kontrol et, ardindan drop.
        PG14_STATUS=$(pg_lsclusters -h | awk '$1=="14" && $2=="main" {print $4}')
        if [[ "${PG14_STATUS}" != "online" ]]; then
            pg_ctlcluster 14 main start >/dev/null 2>&1 || true
            sleep 2
        fi
        USER_DB_COUNT=$(sudo -u postgres psql --cluster 14/main -Atqc \
            "SELECT COUNT(*) FROM pg_database WHERE datname NOT IN ('postgres','template0','template1') AND datistemplate=false;" \
            2>/dev/null || echo "-1")
        if [[ "${USER_DB_COUNT}" == "0" ]]; then
            echo "  PG 14/main cluster bos bulundu — drop ediliyor."
            pg_ctlcluster 14 main stop --force >/dev/null 2>&1 || true
            pg_dropcluster 14 main --stop >/dev/null
        elif [[ "${USER_DB_COUNT}" == "-1" ]]; then
            echo "HATA: PG 14/main cluster sorgulanamadi (baslatma basarisiz)." >&2
            echo "  Manuel kontrol: sudo -u postgres pg_lsclusters" >&2
            exit 2
        else
            echo "HATA: PG 14/main cluster'da ${USER_DB_COUNT} user DB var — migrate veya drop sizin kararinizda." >&2
            echo "  Liste: sudo -u postgres psql --cluster 14/main -l" >&2
            echo "  Bos birakmak icin: sudo pg_dropcluster 14 main --stop" >&2
            exit 2
        fi
    fi

    # PG 16/main cluster port 5432'de degilse yeniden yarat.
    if pg_lsclusters -h 2>/dev/null | awk '{print $1"/"$2"/"$3}' | grep -qx '16/main/5432'; then
        echo "  PG 16/main cluster port 5432'de hazir."
    else
        if pg_lsclusters -h 2>/dev/null | awk '{print $1"/"$2}' | grep -qx '16/main'; then
            echo "  PG 16/main yanlis port'ta — drop + recreate."
            pg_dropcluster 16 main --stop >/dev/null || true
        fi
        pg_createcluster ${PG_VERSION} main --port=5432 --start >/dev/null
        echo "  PG 16/main cluster port 5432'de olusturuldu."
    fi
fi

# TimescaleDB preload — explicit --pg-config, cluster-agnostik degil.
# Dry-run'da "tune --quiet --yes" PG 16 path'ini bulamamisti (kalem 10).
if command -v timescaledb-tune >/dev/null 2>&1; then
    TS_PG_CONFIG="/usr/lib/postgresql/${PG_VERSION}/bin/pg_config"
    if [[ -x "${TS_PG_CONFIG}" ]]; then
        timescaledb-tune --quiet --yes --pg-config="${TS_PG_CONFIG}" >/dev/null 2>&1 || \
            echo "  UYARI: timescaledb-tune hata verdi, shared_preload_libraries manuel dogrulayin."
    fi
fi

# Cluster'i restart et — shared_preload_libraries degisikligi etkili olsun.
systemctl restart postgresql@${PG_VERSION}-main
echo "  TimescaleDB preload aktif, PG ${PG_VERSION} cluster hazir."

# --- 6. Custos kullanicisi ---
echo "[5/11] Custos kullanicisi hazirlaniyor..."
if ! id "$CUSTOS_USER" &>/dev/null; then
    useradd --system --create-home --shell /bin/bash "$CUSTOS_USER"
    echo "  Kullanici '$CUSTOS_USER' olusturuldu."
else
    echo "  Kullanici '$CUSTOS_USER' zaten mevcut."
fi

# --- 7. Kurulum dizinleri + veri/log klasorleri ---
echo "[6/11] Kurulum ve veri dizinleri hazirlaniyor..."
mkdir -p "$INSTALL_DIR"
if [[ -d "$(dirname "$0")/../src" ]]; then
    # shopt dotglob: `*` glob'u .env.example gibi dotfile'lari da kapsasin (kalem 7).
    # Dry-run'da .env.example kopyalanmiyordu -> DB_PASS akisi bozuluyordu.
    # .git klasoru repo kurulumundan gelen kirliligi tasimasin diye --exclude.
    shopt -s dotglob
    rsync -a --exclude='.git' --exclude='.venv' --exclude='__pycache__' \
        "$(dirname "$0")/../" "$INSTALL_DIR/"
    shopt -u dotglob
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

# --- 8. Python venv + bagimoliklar ---
# Torch CPU-only wheel explicit (kalem 6): pilot mini-PC'de GPU yok. PyPI default
# wheel CUDA+cudnn cekiyor (~4-5 GB + 10 dk). torch==2.11.0+cpu sabitli:
#   - setuptools<82 kisitlamasi +cpu wheel'de yok (kalem 12);
#   - A3 denetim setuptools>=78.1.1 korunur (PYSEC-2025-49 path-traversal RCE);
#   - sentence-transformers + faiss otomatik CPU backend.
echo "[7/11] Python sanal ortami kuruluyor (torch CPU-only wheel)..."
if [[ ! -d "$INSTALL_DIR/.venv" ]]; then
    sudo -u "$CUSTOS_USER" python${PYTHON_VERSION} -m venv "$INSTALL_DIR/.venv"
fi
sudo -u "$CUSTOS_USER" "$INSTALL_DIR/.venv/bin/pip" install --upgrade pip -q
# setuptools >=78.1.1 — PYSEC-2025-49 path traversal RCE fix (A3 denetim).
sudo -u "$CUSTOS_USER" "$INSTALL_DIR/.venv/bin/pip" install --upgrade 'setuptools>=78.1.1' -q

# Torch CPU wheel — GPU disinda calisan tum edge deploy'lar icin.
# PIP_EXTRA_INDEX_URL pytorch CPU endeksini pyproject resolution icin de ekler
# (sentence-transformers'in transitive torch ihtiyacini ayni kaynaktan cozer).
export PIP_EXTRA_INDEX_URL=https://download.pytorch.org/whl/cpu
sudo -u "$CUSTOS_USER" \
    PIP_EXTRA_INDEX_URL=https://download.pytorch.org/whl/cpu \
    "$INSTALL_DIR/.venv/bin/pip" install 'torch==2.11.0+cpu' -q

# Ana paket kurulumu (transitive torch artik kurulu, tekrar indirmez)
sudo -u "$CUSTOS_USER" \
    PIP_EXTRA_INDEX_URL=https://download.pytorch.org/whl/cpu \
    "$INSTALL_DIR/.venv/bin/pip" install -e "$INSTALL_DIR" -q
echo "  Bagimoliklar yuklendi (torch 2.11.0+cpu)."

# --- 9. Veritabani + .env ---
echo "[8/11] Veritabani hazirlaniyor..."
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

# --- 10. Seed — AVM Template Pack + bakim checklist'leri ---
echo "[9/11] Seed script'leri calistiriliyor..."
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

# --- 11. VAPID anahtari (otomatik uret + .env'e yaz) ---
echo "[10/11] VAPID anahtarlari kontrol ediliyor..."
if ! grep -qE '^CUSTOS_VAPID_PRIVATE_KEY=.+' "$INSTALL_DIR/.env"; then
    echo "  VAPID anahtarlari uretiliyor..."
    sudo -u "$CUSTOS_USER" "$INSTALL_DIR/.venv/bin/python" \
        "$INSTALL_DIR/scripts/generate_vapid_keys.py" --write-env "$INSTALL_DIR/.env"
else
    echo "  VAPID anahtarlari zaten .env'de mevcut."
fi

# --- 12. Log rotation (placeholder — structlog file handler iin) ---
if [[ -f "$INSTALL_DIR/deploy/logrotate.custos" ]]; then
    cp "$INSTALL_DIR/deploy/logrotate.custos" /etc/logrotate.d/custos
    chmod 644 /etc/logrotate.d/custos
fi

# --- 13. Systemd service'ler (analytics + critical) ---
echo "[11/11] Systemd service'ler kuruluyor..."
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

# --- 14. mDNS (avahi) ---
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
