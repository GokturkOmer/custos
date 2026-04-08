"""Veritabanı sağlık kontrolü scripti.

Kullanım: python scripts/healthcheck.py

Veritabanına bağlanıp SELECT 1 çalıştırır.
Başarılıysa "OK" yazdırıp exit code 0 ile çıkar.
Başarısızsa "FAIL: <sebep>" yazdırıp exit code 1 ile çıkar.
"""

import asyncio
import sys

from custos.shared.config import settings
from custos.shared.database import create_database


async def main() -> int:
    """Sağlık kontrolünü çalıştırır, exit code döndürür."""
    db = create_database(settings)
    try:
        await db.connect()
        healthy = await db.health_check()
        if healthy:
            print("OK")  # noqa: T201
            return 0
        print("FAIL: health_check False döndürdü")  # noqa: T201
        return 1
    except Exception as exc:
        print(f"FAIL: {exc}")  # noqa: T201
        return 1
    finally:
        await db.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
