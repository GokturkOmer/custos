#!/usr/bin/env bash
# Endurance test kurulum scripti — ayrı bir WSL instance'ta tek komutla kurulum.
#
# Kapsam:
#   1. Pre-flight (Ubuntu 22.04+, RAM, disk, sudo)
#   2. deploy/setup.sh çağrısı (TimescaleDB + venv + migration + seed template)
#   3. custos-simulator-endurance.service — 200 tag modunda simülatör
#   4. Ana Custos service başlat (custos.service + custos-critical.service)
#   5. Bulk import: endurance_tags_200.csv → POST /sensors/bulk-import
#   6. endurance_bind_instances.py: 5 instance + tag binding
#   7. (--skip-wait yoksa) 30 dk veri birikmesi bekleme
#   8. train_anomaly_models.py: 5 instance için Isolation Forest
#   9. healthcheck.py --json
#   10. endurance_metrics.py daemon başlat (nohup)
#   11. Tamamlandı banner
#
# Kullanım:
#   sudo bash scripts/endurance_setup.sh
#   sudo bash scripts/endurance_setup.sh --skip-wait       # 30 dk bekleme atlanır
#   sudo bash scripts/endurance_setup.sh --skip-training   # ML eğitim atlanır
#
# Çıkış kodları:
#   0  — Başarı
#   2  — Pre-flight başarısız
#   3  — DB / migration hatası (setup.sh'dan)
#   4  — Permission hatası (root değil)
#   5  — Bulk import / binding hatası
#   6  — ML eğitim başarısız (skip edilebilir)
set -euo pipefail

# --- Parametreler ---
SKIP_WAIT=0
SKIP_TRAINING=0
WAIT_SECONDS=1800  # 30 dakika

for arg in "$@"; do
    case "$arg" in
        --skip-wait) SKIP_WAIT=1 ;;
        --skip-training) SKIP_TRAINING=1 ;;
        --wait-seconds=*) WAIT_SECONDS="${arg#*=}" ;;
        *) echo "HATA: Bilinmeyen argüman '$arg'" >&2; exit 2 ;;
    esac
done

# --- Sabitler ---
CUSTOS_USER="custos"
INSTALL_DIR="/opt/custos"
ENDURANCE_CSV="${INSTALL_DIR}/_personal/pilot/endurance_tags_200.csv"
DASHBOARD_URL="http://localhost:8000"
BULK_IMPORT_URL="${DASHBOARD_URL}/sensors/bulk-import"
METRICS_OUTPUT="/var/log/custos/endurance.csv"
METRICS_PID_FILE="/var/run/custos-endurance-metrics.pid"
METRICS_LOG_FILE="/var/log/custos/endurance_metrics.log"
SIM_UNIT="/etc/systemd/system/custos-simulator-endurance.service"

echo "=== Endurance Test Kurulum Scripti ==="
echo "(Ayrı WSL instance için — v1.0 feature-complete üzerine)"
echo ""

# --- 1. Root kontrolü ---
if [[ $EUID -ne 0 ]]; then
    echo "HATA: Bu script root olarak çalıştırılmalı (sudo bash scripts/endurance_setup.sh)" >&2
    exit 4
fi

# --- 2. Pre-flight (setup.sh zaten ayrıntılı yapıyor ama endurance'a özel ek) ---
echo "[1/11] Endurance ön koşulları kontrol ediliyor..."
TOTAL_RAM_GB=$(awk '/^MemTotal:/ {printf "%.0f", $2/1024/1024}' /proc/meminfo)
AVAIL_GB=$(df -BG --output=avail /opt 2>/dev/null | awk 'NR==2 {sub("G",""); print $1}' || echo 0)
if (( TOTAL_RAM_GB < 2 )); then
    echo "HATA: Endurance için minimum 2 GB RAM gerekli (mevcut ${TOTAL_RAM_GB} GB)" >&2
    exit 2
fi
if (( AVAIL_GB < 8 )); then
    echo "HATA: Endurance için minimum 8 GB disk gerekli (mevcut ${AVAIL_GB} GB)" >&2
    exit 2
fi
echo "  RAM: ${TOTAL_RAM_GB} GB, disk: ${AVAIL_GB} GB — OK."

# --- 3. Ana Custos kurulumu (idempotent) ---
echo "[2/11] deploy/setup.sh çalıştırılıyor (full Custos kurulumu)..."
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." &>/dev/null && pwd)"
bash "${REPO_ROOT}/deploy/setup.sh"
echo "  Ana kurulum tamam."

# --- 4. Endurance CSV hazır mı? (yoksa generator çağır) ---
if [[ ! -f "${ENDURANCE_CSV}" ]]; then
    echo "[3/11] Endurance CSV eksik, üretiliyor..."
    sudo -u "${CUSTOS_USER}" \
        "${INSTALL_DIR}/.venv/bin/python" \
        "${INSTALL_DIR}/scripts/endurance_generate_tags_csv.py" \
        --out "${ENDURANCE_CSV}"
else
    echo "[3/11] Endurance CSV mevcut: ${ENDURANCE_CSV}"
fi

# --- 5. Endurance simülatör systemd unit'i ---
echo "[4/11] custos-simulator-endurance.service kuruluyor..."
cat > "${SIM_UNIT}" <<SIM_EOF
[Unit]
Description=Custos Modbus Simulator (Endurance 200 tags)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${CUSTOS_USER}
WorkingDirectory=${INSTALL_DIR}
EnvironmentFile=${INSTALL_DIR}/.env
Environment=CUSTOS_TAG_COUNT=200
ExecStart=${INSTALL_DIR}/.venv/bin/python -m custos.simulator
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SIM_EOF

systemctl daemon-reload
systemctl enable custos-simulator-endurance.service >/dev/null
systemctl restart custos-simulator-endurance.service
echo "  Simülatör (200 tag) başlatıldı, port 5020."

# --- 6. Ana servisleri başlat ---
echo "[5/11] Ana Custos servisleri başlatılıyor..."
systemctl restart custos.service
systemctl restart custos-critical.service
sleep 5

# Dashboard açık mı?
if ! curl -sf "${DASHBOARD_URL}/dashboard/overview" >/dev/null 2>&1; then
    echo "  UYARI: dashboard yanıt vermedi, 10 sn bekleniyor..."
    sleep 10
    if ! curl -sf "${DASHBOARD_URL}/dashboard/overview" >/dev/null 2>&1; then
        echo "HATA: dashboard ${DASHBOARD_URL} üzerinde açılmadı." >&2
        systemctl status custos.service --no-pager || true
        exit 5
    fi
fi
echo "  Dashboard + collector hazır."

# --- 7. Bulk import ---
echo "[6/11] 200 tag bulk import ediliyor..."
BULK_RESPONSE=$(curl -s -o /tmp/custos-bulk-import.log -w "%{http_code}" \
    -X POST \
    -F "file=@${ENDURANCE_CSV}" \
    -F "mode=insert" \
    "${BULK_IMPORT_URL}")
if [[ "${BULK_RESPONSE}" != "200" ]]; then
    echo "HATA: Bulk import başarısız (HTTP ${BULK_RESPONSE}). Yanıt:" >&2
    tail -40 /tmp/custos-bulk-import.log >&2 || true
    exit 5
fi
echo "  200 tag yüklendi."

# --- 8. Instance + binding ---
echo "[7/11] 5 instance + role binding kuruluyor..."
sudo -u "${CUSTOS_USER}" \
    "${INSTALL_DIR}/.venv/bin/python" \
    "${INSTALL_DIR}/scripts/endurance_bind_instances.py"

# --- 9. Veri birikmesi için bekleme (ML eğitim öncesi) ---
if (( SKIP_WAIT == 0 && SKIP_TRAINING == 0 )); then
    MINUTES=$(( WAIT_SECONDS / 60 ))
    echo "[8/11] ML eğitim için ${MINUTES} dakika veri birikiyor..."
    echo "  (--skip-wait ile atlanabilir — mevcut kurulumda kullanıcı kontrolünde)"
    sleep "${WAIT_SECONDS}"
else
    echo "[8/11] Veri bekleme atlandı (SKIP_WAIT=${SKIP_WAIT}, SKIP_TRAINING=${SKIP_TRAINING})."
fi

# --- 10. ML eğitim ---
if (( SKIP_TRAINING == 0 )); then
    echo "[9/11] Isolation Forest modelleri eğitiliyor..."
    if ! sudo -u "${CUSTOS_USER}" \
            "${INSTALL_DIR}/.venv/bin/python" \
            "${INSTALL_DIR}/scripts/train_anomaly_models.py" --lookback-hours 1; then
        echo "  UYARI: ML eğitim başarısız (veri yetersiz olabilir). Sonra elle tekrar denenebilir."
    fi
else
    echo "[9/11] ML eğitim atlandı (--skip-training)."
fi

# --- 11. Healthcheck ---
echo "[10/11] Healthcheck çalıştırılıyor..."
HC_OUT=$(sudo -u "${CUSTOS_USER}" \
    "${INSTALL_DIR}/.venv/bin/python" \
    "${INSTALL_DIR}/scripts/healthcheck.py" --json || true)
echo "${HC_OUT}" | head -30 || true

# --- 12. Metrics daemon ---
echo "[11/11] endurance_metrics.py daemon başlatılıyor..."
mkdir -p "$(dirname "${METRICS_OUTPUT}")"
chown -R "${CUSTOS_USER}:${CUSTOS_USER}" "$(dirname "${METRICS_OUTPUT}")"

# Eski daemon varsa durdur
if [[ -f "${METRICS_PID_FILE}" ]]; then
    OLD_PID=$(cat "${METRICS_PID_FILE}" 2>/dev/null || echo "")
    if [[ -n "${OLD_PID}" ]] && kill -0 "${OLD_PID}" 2>/dev/null; then
        kill -TERM "${OLD_PID}" || true
        sleep 2
    fi
    rm -f "${METRICS_PID_FILE}"
fi

# nohup ile başlat (PID dosyasına yaz)
sudo -u "${CUSTOS_USER}" bash -c "
    cd ${INSTALL_DIR}
    nohup ${INSTALL_DIR}/.venv/bin/python \
        ${INSTALL_DIR}/scripts/endurance_metrics.py \
        --out ${METRICS_OUTPUT} \
        > ${METRICS_LOG_FILE} 2>&1 &
    echo \$! > ${METRICS_PID_FILE}
"
sleep 2
if [[ -f "${METRICS_PID_FILE}" ]] && kill -0 "$(cat "${METRICS_PID_FILE}")" 2>/dev/null; then
    echo "  Metrics daemon çalışıyor (PID $(cat "${METRICS_PID_FILE}"))."
else
    echo "  UYARI: Metrics daemon başlatılamadı, log: ${METRICS_LOG_FILE}" >&2
fi

# --- Tamamlandı banner ---
START_DATE=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
cat <<BANNER

================================================================
  ENDURANCE TEST KURULUMU TAMAMLANDI
================================================================
  Başlangıç (UTC)    : ${START_DATE}
  Simülatör          : 200 tag @ localhost:5020 (CUSTOS_TAG_COUNT=200)
  Dashboard          : ${DASHBOARD_URL}/dashboard
  Metrics CSV        : ${METRICS_OUTPUT}
  Metrics daemon log : ${METRICS_LOG_FILE}
  Günlük kontrol     : ${INSTALL_DIR}/.venv/bin/python \\
                       ${INSTALL_DIR}/scripts/endurance_daily_check.py \\
                       --csv ${METRICS_OUTPUT}
  Simülatör service  : systemctl status custos-simulator-endurance.service
  Ana service'ler    : systemctl status custos.service custos-critical.service

  7 gün kesintisiz çalıştırın. Her sabah daily_check + dashboard
  üzerinde 15 dk operatör simülasyonu (kılavuzdaki adımlar) önerilir.
================================================================
BANNER

exit 0
