#!/usr/bin/env bash
# Custos self-signed TLS sertifikası üretici (V11-102 / P-03).
#
# Üretilen dosyalar /etc/custos/tls/{cert.pem,key.pem} altına yazılır.
# Caddy reverse proxy bu sertifikayı tüketir; LAN trafiği şifrelenir.
# Browser ilk girişte "güvenli değil" uyarısı verir (TOFU); kullanıcı
# kabul edince sertifika kaydedilir, bir daha sormaz.
#
# Pilot LAN'da ortam değişkeni:
#   CUSTOS_HOST_IP=192.168.x.y   (.env dosyasından okunur)
#
# Argüman olarak da verilebilir:
#   sudo bash scripts/generate_tls_cert.sh 192.168.1.10
#
# 10 yıl geçerli (3650 gün) — internet bağlantısı / Let's Encrypt yenileme
# zorunluluğu yok (K9: program internete açık olmasın).
#
# Idempotent: Mevcut cert varsa dokunmaz, --force ile yeniden üretilir.
#
# Çıkış kodları:
#   0  — Başarı (yeni cert üretildi veya zaten mevcut)
#   2  — Önkoşul eksik (root değil, CUSTOS_HOST_IP yok)
#   3  — openssl hatası

set -euo pipefail

CERT_DIR="/etc/custos/tls"
CERT_FILE="${CERT_DIR}/cert.pem"
KEY_FILE="${CERT_DIR}/key.pem"
DAYS_VALID=3650

FORCE=0
HOST_IP=""

# --- Argüman parse ---
for arg in "$@"; do
    case "$arg" in
        --force) FORCE=1 ;;
        -h|--help)
            cat <<'EOF'
Custos TLS self-signed cert üretici.

Kullanım:
  sudo bash scripts/generate_tls_cert.sh [IP] [--force]

Çevresel değişkenler:
  CUSTOS_HOST_IP   Sertifikanın CN ve SAN'ı (gerekli)

Argümanlar:
  IP       CUSTOS_HOST_IP override
  --force  Mevcut cert'i sil, yenisini üret
EOF
            exit 0
            ;;
        *) HOST_IP="$arg" ;;
    esac
done

# --- Root kontrolü ---
if [[ $EUID -ne 0 ]]; then
    echo "HATA: Bu script root olarak çalıştırılmalı (sudo bash scripts/generate_tls_cert.sh)" >&2
    exit 2
fi

# --- Host IP — argüman > env > .env dosyası ---
if [[ -z "$HOST_IP" ]]; then
    HOST_IP="${CUSTOS_HOST_IP:-}"
fi
if [[ -z "$HOST_IP" && -f /opt/custos/.env ]]; then
    HOST_IP=$(grep -E '^CUSTOS_HOST_IP=' /opt/custos/.env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'")
fi
if [[ -z "$HOST_IP" ]]; then
    echo "HATA: CUSTOS_HOST_IP belirtilmedi." >&2
    echo "  .env'e ekle (CUSTOS_HOST_IP=192.168.x.y) veya argüman geç:" >&2
    echo "  sudo bash scripts/generate_tls_cert.sh 192.168.1.10" >&2
    exit 2
fi

# IP formatı temel kontrolü — yanlış değer cert'i bozmasın.
if ! [[ "$HOST_IP" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "HATA: '$HOST_IP' geçerli bir IPv4 adresi gibi görünmüyor." >&2
    exit 2
fi

# --- openssl varlık kontrolü ---
if ! command -v openssl >/dev/null 2>&1; then
    echo "HATA: openssl bulunamadı. apt install -y openssl" >&2
    exit 2
fi

# --- Cert dizini hazırla ---
mkdir -p "$CERT_DIR"
chmod 750 "$CERT_DIR"

# --- Idempotent kontrolü ---
if [[ -f "$CERT_FILE" && -f "$KEY_FILE" && $FORCE -eq 0 ]]; then
    # Mevcut cert'in CN/SAN'ı uyuyor mu?
    EXISTING_SAN=$(openssl x509 -in "$CERT_FILE" -noout -ext subjectAltName 2>/dev/null \
        | grep -oE 'IP Address:[0-9.]+' | head -1 | cut -d: -f2 || echo "")
    if [[ "$EXISTING_SAN" == "$HOST_IP" ]]; then
        echo "  Mevcut cert ${HOST_IP} için uygun, yeniden üretilmedi (--force ile zorla)."
        # Doğru sahip / izinler — idempotent şekilde tekrar uygula.
        chmod 644 "$CERT_FILE"
        chmod 600 "$KEY_FILE"
        exit 0
    fi
    echo "  Mevcut cert farklı IP (${EXISTING_SAN:-bilinmiyor}) için, yeniden üretiliyor."
fi

# --- Cert üret ---
# /CN=${HOST_IP}      Common Name (legacy clients)
# subjectAltName      Modern browser zorunlu (RFC 2818)
# rsa:4096            Pilot mini PC'de tek seferlik üretim, hız önemsiz
# -nodes              Key parolasız (caddy reload sırasında prompt olmasın)
echo "  Self-signed sertifika üretiliyor: CN=${HOST_IP}, ${DAYS_VALID} gün geçerli..."
openssl req -x509 -nodes -days "$DAYS_VALID" -newkey rsa:4096 \
    -keyout "$KEY_FILE" \
    -out "$CERT_FILE" \
    -subj "/CN=${HOST_IP}" \
    -addext "subjectAltName=IP:${HOST_IP},DNS:custos.local" \
    -addext "keyUsage=digitalSignature,keyEncipherment" \
    -addext "extendedKeyUsage=serverAuth" \
    >/dev/null 2>&1 || {
        echo "HATA: openssl sertifika üretemedi." >&2
        exit 3
    }

# --- Sahip / izinler ---
# Caddy non-root çalışır; cert read için herkese açık (zaten public veri),
# key sadece caddy ve root okuyabilir (mode 640, grup caddy).
chmod 644 "$CERT_FILE"
chmod 640 "$KEY_FILE"
if id -nG root | grep -qw caddy 2>/dev/null || getent group caddy >/dev/null 2>&1; then
    chgrp caddy "$KEY_FILE" 2>/dev/null || true
fi

echo "  Üretildi: $CERT_FILE (10 yıl geçerli)"
echo "  Anahtar : $KEY_FILE (mode 640)"
