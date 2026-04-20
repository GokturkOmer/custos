"""Continuous aggregate'leri tam aralıkta backfill eder.

Kullanım: python scripts/refresh_continuous_aggregates.py

Migration 025 sonrası bir kez elle çalıştırılır. Mevcut ``tag_readings``
verisini önce ``tag_readings_1min``, sonra hierarchical sıraya göre
``tag_readings_1hour`` agregatına materialize eder. Boş DB'de no-op.

``CALL refresh_continuous_aggregate()`` açık transaction içinde
çalışamadığı için bu script alembic migration'a gömülmedi; asyncpg
connection üzerinde explicit transaction açılmadan çalışır.

Exit kodu:
- 0: Başarılı
- 1: DB bağlantı hatası veya CALL başarısız
"""

import asyncio
import sys

import asyncpg

from custos.shared.config import settings

AGGREGATES: tuple[str, ...] = ("tag_readings_1min", "tag_readings_1hour")


async def main() -> int:
    """Agregat tablolarını tam aralıkta refresh eder."""
    try:
        conn = await asyncpg.connect(dsn=settings.database_url)
    except Exception as exc:
        print(f"FAIL: Veritabanına bağlanılamadı — {exc}")  # noqa: T201
        return 1

    try:
        for agg in AGGREGATES:
            print(f"[{agg}] refresh_continuous_aggregate(NULL, NULL)...")  # noqa: T201
            await conn.execute(
                f"CALL refresh_continuous_aggregate('{agg}', NULL, NULL);"
            )
            row = await conn.fetchrow(
                f"SELECT COUNT(*) AS n FROM {agg}"  # noqa: S608
            )
            count = row["n"] if row else 0
            print(f"[{agg}] OK — {count} satır materialize edildi")  # noqa: T201
    except Exception as exc:
        print(f"FAIL: refresh hatası — {exc}")  # noqa: T201
        return 1
    finally:
        await conn.close()

    print("Sonuç: tüm agregatlar refresh edildi.")  # noqa: T201
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
