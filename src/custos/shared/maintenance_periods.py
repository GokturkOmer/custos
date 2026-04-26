"""Periyodik bakım için sonraki vade tarihi hesaplayan pure fonksiyonlar.

Tüm hesaplamalar UTC datetime üzerinde yapılır (CLAUDE.md kuralı).
Ay/yıl bazlı ilerletmede ay sonu ve artık yıl edge case'leri standard
lib (calendar.monthrange) ile ele alınır — ekstra bağımlılık yok.
"""

from __future__ import annotations

from calendar import monthrange
from datetime import datetime, timedelta


def compute_next_due_at(
    current: datetime,
    kind: str,
    value: int = 1,
) -> datetime:
    """Bir schedule'ın sonraki vade tarihini hesaplar.

    Args:
        current: Şu anki vade (genellikle `schedule.next_due_at`).
        kind: 'daily' / 'weekly' / 'monthly' / 'yearly' / 'custom_days'.
        value: Multiplier — kind'ın kaç birim ilerleyeceği (örn.
            kind='monthly', value=3 → 3 ay sonrası).

    Returns:
        Yeni vade tarihi (current timezone'u korunur — UTC beklenir).

    Raises:
        ValueError: kind bilinmeyense veya value < 1 ise.
    """
    if value < 1:
        msg = f"period_value en az 1 olmalı, verilen: {value}"
        raise ValueError(msg)

    if kind == "daily":
        return current + timedelta(days=value)
    if kind == "weekly":
        return current + timedelta(days=7 * value)
    if kind == "custom_days":
        return current + timedelta(days=value)
    if kind == "monthly":
        total_months = (current.month - 1) + value
        new_year = current.year + total_months // 12
        new_month = total_months % 12 + 1
        # Ay sonu koruması: örn. 31 Ocak + 1 ay = 28/29 Şubat
        last_day = monthrange(new_year, new_month)[1]
        new_day = min(current.day, last_day)
        return current.replace(year=new_year, month=new_month, day=new_day)
    if kind == "yearly":
        new_year = current.year + value
        # Artık yıl koruması: 29 Şubat → yeni yıl Şubat'ında 28 veya 29
        last_day = monthrange(new_year, current.month)[1]
        new_day = min(current.day, last_day)
        return current.replace(year=new_year, day=new_day)

    msg = f"Bilinmeyen period_kind: {kind}"
    raise ValueError(msg)
