#!/usr/bin/env bash
# Endurance modpoll periodic sanity check (cron tarafından her 5 dk çağrılır).
#
# Modbus TCP request bir seferde max 125 holding register okuyabilir (protokol
# kısıtı). Bu yüzden 200 tag'i 4 grup × 50 register olarak okuruz —
# kategori başına ayrı log satırı, debug için kolay.
#
# Cron satırı:
#   */5 * * * * /home/orientpro/projeler/custos/scripts/modpoll_endurance_sanity.sh \
#       >> /var/log/custos-endurance/modpoll-sanity.log 2>&1
#
# Çıktı:
#   /var/log/custos-endurance/modpoll-sanity.log

set -uo pipefail
HOST="127.0.0.1"
PORT="502"
TS=$(date -u --iso-8601=seconds)

echo ""
echo "═══ ${TS} ═══"

# Sıcaklık (Reg 1-50)
echo "--- TEMP (1-50) ---"
modpoll -m tcp -p "${PORT}" -r 1 -c 50 -1 "${HOST}" 2>&1 | tail -52 | head -50

# Basınç (Reg 51-100)
echo "--- PRES (51-100) ---"
modpoll -m tcp -p "${PORT}" -r 51 -c 50 -1 "${HOST}" 2>&1 | tail -52 | head -50

# Enerji (Reg 101-150)
echo "--- ENERGY (101-150) ---"
modpoll -m tcp -p "${PORT}" -r 101 -c 50 -1 "${HOST}" 2>&1 | tail -52 | head -50

# RPM + Status (Reg 151-200)
echo "--- RPM+STATUS (151-200) ---"
modpoll -m tcp -p "${PORT}" -r 151 -c 50 -1 "${HOST}" 2>&1 | tail -52 | head -50

echo "═══ ${TS} END ═══"
