#!/usr/bin/env python3
"""VAPID key pair üretim script'i.

Web Push bildirimleri için ECDSA P-256 key pair üretir ve
(opsiyonel olarak) .env dosyasına idempotent şekilde yazar.

Kullanım:
    # stdout'a yazdır (manuel kopyala-yapıştır):
    python scripts/generate_vapid_keys.py

    # .env'e otomatik yaz (idempotent — mevcut anahtarlara dokunmaz):
    python scripts/generate_vapid_keys.py --write-env /opt/custos/.env

    # .env'deki anahtarları override et (nadir durumda):
    python scripts/generate_vapid_keys.py --write-env /opt/custos/.env --force
"""

from __future__ import annotations

import argparse
import base64
import os
import re
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid  # type: ignore[import-untyped]

# .env içinde VAPID anahtar satırlarını eşleyen regex.
# "=" sonrasında boş veya atanmamış bir değer varsa yine match'ler — replace hedefi bu.
_PRIVATE_RE = re.compile(r"^CUSTOS_VAPID_PRIVATE_KEY=(.*)$")
_PUBLIC_RE = re.compile(r"^CUSTOS_VAPID_PUBLIC_KEY=(.*)$")


def _generate_keys() -> tuple[str, str]:
    """ECDSA P-256 pair üret, base64url encode (padding strip) ile döndür.

    Public key: 65 byte uncompressed point (X9.62 — Web Push VAPID RFC 8292).
    Private key: 32 byte raw secret value.
    """
    vapid = Vapid()
    vapid.generate_keys()
    raw_pub = vapid.public_key.public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    public_key = base64.urlsafe_b64encode(raw_pub).decode("ascii").rstrip("=")
    raw_priv = vapid.private_key.private_numbers().private_value.to_bytes(32, byteorder="big")
    private_key = base64.urlsafe_b64encode(raw_priv).decode("ascii").rstrip("=")
    return private_key, public_key


def _print_stdout(private_key: str, public_key: str) -> None:
    """Legacy davranış — stdout'a 4 satırlık yapıştırma blogu."""
    print("VAPID Key Pair üretildi.")  # noqa: T201
    print()  # noqa: T201
    print("Aşağıdaki satırları .env dosyanıza ekleyin:")  # noqa: T201
    print()  # noqa: T201
    print(f"CUSTOS_VAPID_PRIVATE_KEY={private_key}")  # noqa: T201
    print(f"CUSTOS_VAPID_PUBLIC_KEY={public_key}")  # noqa: T201


def _has_existing_keys(lines: list[str]) -> bool:
    """.env'de iki anahtarın da boş-olmayan değeri var mı?"""
    has_private = False
    has_public = False
    for line in lines:
        stripped = line.rstrip("\r\n")
        priv_match = _PRIVATE_RE.match(stripped)
        pub_match = _PUBLIC_RE.match(stripped)
        if priv_match and priv_match.group(1).strip():
            has_private = True
        if pub_match and pub_match.group(1).strip():
            has_public = True
    return has_private and has_public


def _write_env_keys(env_path: Path, private_key: str, public_key: str, force: bool) -> bool:
    """VAPID anahtarlarını .env'e yaz — atomik (temp + rename). Return True=yazıldı."""
    if not env_path.exists():
        msg = f".env dosyası bulunamadı: {env_path}"
        raise FileNotFoundError(msg)

    original = env_path.read_text(encoding="utf-8")
    lines = original.splitlines(keepends=True)

    if _has_existing_keys(lines) and not force:
        return False

    # Mevcut satırı replace et; yoksa sona append.
    new_lines: list[str] = []
    private_done = False
    public_done = False
    for line in lines:
        stripped = line.rstrip("\r\n")
        if _PRIVATE_RE.match(stripped):
            new_lines.append(f"CUSTOS_VAPID_PRIVATE_KEY={private_key}\n")
            private_done = True
        elif _PUBLIC_RE.match(stripped):
            new_lines.append(f"CUSTOS_VAPID_PUBLIC_KEY={public_key}\n")
            public_done = True
        else:
            new_lines.append(line)
    if not private_done:
        new_lines.append(f"CUSTOS_VAPID_PRIVATE_KEY={private_key}\n")
    if not public_done:
        new_lines.append(f"CUSTOS_VAPID_PUBLIC_KEY={public_key}\n")

    # Atomik yazma: temp dosya + rename (POSIX atomic)
    tmp_path = env_path.with_suffix(env_path.suffix + ".tmp")
    tmp_path.write_text("".join(new_lines), encoding="utf-8")
    os.chmod(tmp_path, 0o600)
    tmp_path.replace(env_path)
    return True


def _parse_args() -> argparse.Namespace:
    """CLI argümanları."""
    parser = argparse.ArgumentParser(description="VAPID key pair üret")
    parser.add_argument(
        "--write-env",
        type=Path,
        metavar="PATH",
        help=".env dosyasına idempotent yaz (mevcut anahtarlara dokunmaz)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="--write-env ile birlikte: mevcut anahtarları üzerine yaz",
    )
    return parser.parse_args()


def main() -> int:
    """Argümanlara göre stdout veya .env yazma moduna geç."""
    args = _parse_args()
    private_key, public_key = _generate_keys()

    if args.write_env is None:
        _print_stdout(private_key, public_key)
        return 0

    try:
        written = _write_env_keys(args.write_env, private_key, public_key, args.force)
    except FileNotFoundError as exc:
        print(f"HATA: {exc}", file=sys.stderr)  # noqa: T201
        return 1

    if written:
        print(f"VAPID anahtarları {args.write_env} dosyasına yazıldı.")  # noqa: T201
    else:
        print(  # noqa: T201
            f"{args.write_env} içinde VAPID anahtarları zaten mevcut — "
            f"dokunulmadı (--force ile zorla).",
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
