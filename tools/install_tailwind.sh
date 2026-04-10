#!/usr/bin/env bash
# Tailwind CSS standalone binary kurulumu (v3.4.17)
set -euo pipefail

VERSION="v3.4.17"
PLATFORM="linux-x64"
URL="https://github.com/tailwindlabs/tailwindcss/releases/download/${VERSION}/tailwindcss-${PLATFORM}"
DEST="$(dirname "$0")/tailwindcss"

echo "Tailwind CSS ${VERSION} indiriliyor..."
curl -sL -o "${DEST}" "${URL}"
chmod +x "${DEST}"
echo "Kurulum tamamlandı: ${DEST}"
