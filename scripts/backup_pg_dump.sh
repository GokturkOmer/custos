#!/usr/bin/env bash
# Custos haftalik PostgreSQL pg_dump (V11-109 / P-06).
#
# Cron: Pazar 03:00 TRT (setup.sh /etc/cron.d/custos-backup).
#
# Yedek: /var/custos/backup/pg/custos-YYYYMMDD.sql.gz, chmod 600.
# Retention: 30 gun (find -mtime +30 -delete).
#
# DSN onceligi:
#   1. CUSTOS_DB_ADMIN_DSN (.env'den okunur — V11-106 custos_admin user'i)
#   2. Eski tek-user kurulumlari icin POSTGRES_PASSWORD ile fallback.
#
# Owner yetkisi pg_dump butunlugu icin gerekli (custos_admin DB owner).

set -euo pipefail

INSTALL_DIR="${CUSTOS_INSTALL_DIR:-/opt/custos}"
ENV_FILE="${CUSTOS_ENV_FILE:-${INSTALL_DIR}/.env}"
BACKUP_DIR="${CUSTOS_BACKUP_PG_DIR:-/var/custos/backup/pg}"
RETENTION_DAYS="${CUSTOS_BACKUP_PG_RETENTION_DAYS:-30}"

mkdir -p "$BACKUP_DIR"
chmod 750 "$BACKUP_DIR"

# DSN cozumu — admin DSN onceligi
ADMIN_DSN=""
if [[ -f "$ENV_FILE" ]]; then
    ADMIN_DSN=$(grep -E '^CUSTOS_DB_ADMIN_DSN=' "$ENV_FILE" | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'")
fi

TIMESTAMP=$(date +%Y%m%d)
OUT="${BACKUP_DIR}/custos-${TIMESTAMP}.sql.gz"

if [[ -n "$ADMIN_DSN" ]]; then
    # custos_admin DSN ile pg_dump (--no-owner --no-acl restore'u portable kilar).
    pg_dump --no-owner --no-acl --dbname="$ADMIN_DSN" | gzip > "$OUT"
else
    # Fallback: eski tek-user akisi — POSTGRES_PASSWORD .env'den okunup PGPASSWORD ile gecirilir.
    if [[ ! -f "$ENV_FILE" ]]; then
        echo "HATA: .env bulunamadi: $ENV_FILE" >&2
        exit 1
    fi
    PG_PASS=$(grep -E '^POSTGRES_PASSWORD=' "$ENV_FILE" | head -1 | cut -d= -f2-)
    PG_USER=$(grep -E '^POSTGRES_USER=' "$ENV_FILE" | head -1 | cut -d= -f2-)
    PG_DB=$(grep -E '^POSTGRES_DB=' "$ENV_FILE" | head -1 | cut -d= -f2-)
    PG_USER="${PG_USER:-custos}"
    PG_DB="${PG_DB:-custos}"
    if [[ -z "$PG_PASS" ]]; then
        echo "HATA: CUSTOS_DB_ADMIN_DSN da POSTGRES_PASSWORD da bulunamadi." >&2
        exit 1
    fi
    PGPASSWORD="$PG_PASS" pg_dump --no-owner --no-acl \
        -h localhost -U "$PG_USER" -d "$PG_DB" | gzip > "$OUT"
fi

chmod 600 "$OUT"

# 30 gun retention (default; CUSTOS_BACKUP_PG_RETENTION_DAYS ile override edilebilir).
find "$BACKUP_DIR" -name "custos-*.sql.gz" -mtime +"$RETENTION_DAYS" -delete

SIZE=$(du -h "$OUT" | cut -f1)
echo "[backup_pg_dump] OK $OUT ($SIZE)"
