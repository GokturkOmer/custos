#!/usr/bin/env bash
# Custos wind pivot Faz 1.5 — end-to-end demo orchestrator.
#
# Iki modu vardir:
# 1) --offline (default): CSV'den dogrudan egitim + CARE benchmark + lead-time.
#    Hizli, reproducible, sudo gerektirmez. Tek seferlik calisma ~5-10 dk.
# 2) --pipeline: 5 systemd unit + replay simulator + DB + offline post-process.
#    Faz 1.1-1.3'te dogrulandi; Faz 1.5'te kapanis raporlama icin offline
#    yeterli (ayni numarik sonuclari uretir).
#
# AVM production'a hicbir sekilde dokunulmaz: tum yazma islemleri
# 'custos_wind' DB'sine ve 'data/models/' klasorune kapsar.
#
# Kullanim:
#   ./scripts/run_wind_pivot_demo.sh            # offline (default)
#   ./scripts/run_wind_pivot_demo.sh --offline
#   ./scripts/run_wind_pivot_demo.sh --pipeline # full systemd + replay (sudo gerekir)

set -euo pipefail

# --- Yol konfigurasyonu (worktree-bagimsiz) ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Veriler ve modeller MAIN worktree'de yasiyor (gitignore'da). Eger bu betik
# bir Claude worktree'sinden cagriliyorsa, ana repo'daki _personal/ klasorune
# isaret etmeliyiz.
PERSONAL_BASE="${CUSTOS_WIND_PERSONAL_BASE:-/home/orientpro/projeler/custos/_personal/wind_pivot}"
DATASETS_DIR="${PERSONAL_BASE}/raw/CARE_To_Compare/Wind Farm A/datasets"
EVENT_INFO="${PERSONAL_BASE}/raw/CARE_To_Compare/Wind Farm A/event_info.csv"
TAG_MAP="${PERSONAL_BASE}/tag_map_farm_a.csv"
REPORTS_DIR="${PERSONAL_BASE}/reports"
MODELS_DIR="${REPO_ROOT}/data/models"

# Python (main venv)
VENV_PY="/home/orientpro/projeler/custos/.venv/bin/python"

# PYTHONPATH override — bu worktree'nin src'ini one al
export PYTHONPATH="${REPO_ROOT}/src:${PYTHONPATH:-}"

MODE="offline"
ASSETS="0,10,11,13,21"
EVENT_FOR_LEADTIME="0"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --offline)   MODE="offline"; shift ;;
        --pipeline)  MODE="pipeline"; shift ;;
        --assets)    ASSETS="$2"; shift 2 ;;
        --event)     EVENT_FOR_LEADTIME="$2"; shift 2 ;;
        -h|--help)
            grep '^#' "$0" | head -25
            exit 0
            ;;
        *) echo "Bilinmeyen argument: $1" >&2; exit 2 ;;
    esac
done

# --- Sanity check: dataset gercekten varsa ilerle ---
if [[ ! -d "${DATASETS_DIR}" ]]; then
    echo "HATA: Dataset dizini bulunamadi: ${DATASETS_DIR}" >&2
    echo "  Faz 0 keşfini tamamla veya CUSTOS_WIND_PERSONAL_BASE'i set et." >&2
    exit 1
fi
if [[ ! -f "${EVENT_INFO}" ]]; then
    echo "HATA: event_info.csv bulunamadi: ${EVENT_INFO}" >&2
    exit 1
fi
if [[ ! -x "${VENV_PY}" ]]; then
    echo "HATA: venv Python bulunamadi: ${VENV_PY}" >&2
    exit 1
fi

mkdir -p "${MODELS_DIR}" "${REPORTS_DIR}"

echo "════════════════════════════════════════════════════════════════"
echo "  Custos Wind Pivot — Faz 1.5 End-to-End Demo"
echo "════════════════════════════════════════════════════════════════"
echo "Mode             : ${MODE}"
echo "Assets           : ${ASSETS}"
echo "Datasets dir     : ${DATASETS_DIR}"
echo "Event info       : ${EVENT_INFO}"
echo "Models dir       : ${MODELS_DIR}"
echo "Reports dir      : ${REPORTS_DIR}"
echo "Lead-time event  : ${EVENT_FOR_LEADTIME}"
echo "─────────────────────────────────────────────────────────────────"

if [[ "${MODE}" == "pipeline" ]]; then
    echo
    echo "[pipeline] Bu mod 5 systemd unit + replay simulator + 10 dk wait gerektirir."
    echo "[pipeline] Faz 1.5 kapanis kabul kriterleri offline mod ile karsilanir;"
    echo "[pipeline] pipeline modu Faz 2'ye birakildi (gerekce: ayni numarik sonuc)."
    echo
    echo "Pipeline adimlari (referans, calistirilmiyor):"
    echo "  1. set -a; source ${PERSONAL_BASE}/.env.wind; set +a"
    echo "  2. sudo systemctl start custos-wind-diagslave (port 5021)"
    echo "  3. sudo systemctl start custos-wind          (uvicorn 8003)"
    echo "  4. sudo systemctl start custos-wind-critical"
    echo "  5. sudo systemctl start custos-wind-metrics"
    echo "  6. ${VENV_PY} scripts/csv_replay_simulator.py \\"
    echo "       --csv \"${DATASETS_DIR}/${EVENT_FOR_LEADTIME}.csv\" \\"
    echo "       --tag-map \"${TAG_MAP}\" --diagslave-port 5021 --speed 1000 ..."
    echo "  7. wait ~10 dk (gercek-zaman, CSV 1 yil 16 gunu hizlandirilmis)"
    echo "  8. custos_wind DB'den anomaly_scores + alarm_events sorgu"
    echo "  9. CARE scorer ile yeniden degerlendirme (validate script)"
    echo " 10. sudo systemctl stop custos-wind-*"
    echo
    echo "Pipeline secildi ama Faz 1.5 amaciyla offline'a fallback ediyorum."
    echo
fi

# --- Adim 1: Modelleri egit ---
echo
echo "─── Adim 1/3: CSV'den IF + AE egitimi (asset=${ASSETS}) ───"
"${VENV_PY}" "${SCRIPT_DIR}/train_wind_models_from_csv.py" \
    --datasets-dir "${DATASETS_DIR}" \
    --event-info "${EVENT_INFO}" \
    --models-dir "${MODELS_DIR}" \
    --assets "${ASSETS}"

# --- Adim 2: CARE benchmark (tum 22 event) ---
echo
echo "─── Adim 2/3: CARE benchmark (22 event, IF + AE + Combined + 3 baseline) ───"
"${VENV_PY}" "${SCRIPT_DIR}/validate_models_on_care.py" \
    --event-info "${EVENT_INFO}" \
    --datasets-dir "${DATASETS_DIR}" \
    --models-dir "${MODELS_DIR}" \
    --report-path "${REPORTS_DIR}/03_care_results.md" \
    --limit 22

# --- Adim 3: Lead-time analizi (event 0 + tum anomaly event'ler) ---
echo
echo "─── Adim 3/3: Lead-time analizi (anomaly event'ler) ───"
"${VENV_PY}" "${SCRIPT_DIR}/analyze_event_leadtime.py" \
    --event-info "${EVENT_INFO}" \
    --datasets-dir "${DATASETS_DIR}" \
    --models-dir "${MODELS_DIR}" \
    --tc 12 \
    --output "${REPORTS_DIR}/04_leadtime_all.md"

echo
echo "─── Lead-time (sadece event ${EVENT_FOR_LEADTIME}, terminal cikti) ───"
"${VENV_PY}" "${SCRIPT_DIR}/analyze_event_leadtime.py" \
    --event-info "${EVENT_INFO}" \
    --datasets-dir "${DATASETS_DIR}" \
    --models-dir "${MODELS_DIR}" \
    --event-id "${EVENT_FOR_LEADTIME}" \
    --tc 12

echo
echo "════════════════════════════════════════════════════════════════"
echo "  Tamamlandi"
echo "════════════════════════════════════════════════════════════════"
echo "  CARE rapor       : ${REPORTS_DIR}/03_care_results.md"
echo "  Lead-time rapor  : ${REPORTS_DIR}/04_leadtime_all.md"
echo "  Modeller         : ${MODELS_DIR}/anomaly_*.joblib + autoencoder_*_wind.joblib"
echo "════════════════════════════════════════════════════════════════"
