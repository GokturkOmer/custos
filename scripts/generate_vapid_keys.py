#!/usr/bin/env python3
"""VAPID key pair üretim script'i.

Web Push bildirimleri için ECDSA key pair üretir.
Üretilen anahtarları .env dosyasına yapıştırmanız gerekir.

Kullanım:
    python scripts/generate_vapid_keys.py
"""

from __future__ import annotations

import base64

from py_vapid import Vapid  # type: ignore[import-untyped]


def main() -> None:
    """VAPID key pair üretir ve stdout'a yazdırır."""
    vapid = Vapid()
    vapid.generate_keys()

    # Raw public key (65 byte uncompressed point)
    raw_pub = vapid.public_key.public_bytes_raw()
    public_key = base64.urlsafe_b64encode(raw_pub).decode("ascii").rstrip("=")

    # Raw private key (32 byte)
    raw_priv = vapid.private_key.private_bytes_raw()
    private_key = base64.urlsafe_b64encode(raw_priv).decode("ascii").rstrip("=")

    print("VAPID Key Pair üretildi.")  # noqa: T201
    print()  # noqa: T201
    print("Aşağıdaki satırları .env dosyanıza ekleyin:")  # noqa: T201
    print()  # noqa: T201
    print(f"CUSTOS_VAPID_PRIVATE_KEY={private_key}")  # noqa: T201
    print(f"CUSTOS_VAPID_PUBLIC_KEY={public_key}")  # noqa: T201


if __name__ == "__main__":
    main()
