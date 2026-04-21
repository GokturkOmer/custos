"""Query guard — aşırı geniş sorguları reddet veya daha ucuz katmana zorla.

F11 Paket H kapsamında eklendi. Kullanıcı veya dashboard tarafından tetiklenen
sorgular bilinçsizce sistemi yavaşlatmasın diye (200 tag × 2 yıl × ham gibi)
`query_readings_auto` içinde çağrılır. Kural matrisi `Settings`'ten okunan
eşiklerle yönetilir — test ve pilot ortamında override edilebilir.

Karar mantığı katmana göre:
    requested_layer="raw"    → tag_count × time_range_days > raw_max    → forced "1min"
    requested_layer="1min"   → tag_count × time_range_days > 1min_max   → forced "1hour"
    requested_layer="1hour"  → time_range_days > 1hour_max_days         → reject

Guard pure fonksiyondur (DB erişimi yok). `QueryGuardError` reject yolunda
yükselir; dashboard handler'ı bu hatayı HTTP 400'e çevirir.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from custos.shared.config import Settings, settings

Layer = Literal["raw", "1min", "1hour"]


@dataclass(frozen=True)
class GuardDecision:
    """Guard'ın tek sorgu için verdiği karar.

    Alanlar:
        allowed: Sorgu kabul edildi mi (reject=False durumu).
        forced_aggregate: Bir üst katmana zorlama varsa ismi ("1min"/"1hour");
            yoksa None (orijinal katman kullanılır).
        reason: Karar açıklaması (log + UI toast metni için).
    """

    allowed: bool
    forced_aggregate: Layer | None
    reason: str


class QueryGuardError(Exception):
    """Guard sorguyu reddettiğinde yükselir.

    Mesaj kullanıcıya gösterilmek üzere Türkçe ve eyleme yönelik yazılır
    ("çok uzun pencere, parçalı sorgu yapın" gibi). Dashboard handler'ı
    HTMX 400 yanıtında `detail` alanı olarak aktarır.
    """


def evaluate_query(
    tag_count: int,
    time_range_days: float,
    requested_layer: Layer,
    settings_obj: Settings | None = None,
) -> GuardDecision:
    """Sorguyu değerlendirir: allow / force-aggregate / reject.

    Parametreler:
        tag_count: Sorguya dahil edilen tag sayısı (>=1).
        time_range_days: Pencere büyüklüğü gün cinsinden (float, saat
            olan 1h → 1/24 ≈ 0.0417).
        requested_layer: Başlangıç katmanı ("raw" | "1min" | "1hour").
        settings_obj: Eşik override'ları için Settings instance'ı. Test
            ortamında özel eşikler geçmek için kullanılır; default global
            `settings`.

    Döndürür:
        GuardDecision — allowed, forced_aggregate, reason alanlarıyla.

    Kurallar (Paket H spec'i):
        raw   + (tag_count × days) > raw_max     → forced "1min"
        1min  + (tag_count × days) > 1min_max    → forced "1hour"
        1hour + days > 1hour_max_days            → reject
    """
    s = settings_obj if settings_obj is not None else settings
    raw_max = s.query_guard_raw_max_tag_days
    min1_max = s.query_guard_1min_max_tag_days
    hour1_max_days = s.query_guard_1hour_max_days

    load = max(1, tag_count) * max(0.0, time_range_days)

    if requested_layer == "raw":
        if load > raw_max:
            return GuardDecision(
                allowed=True,
                forced_aggregate="1min",
                reason=(
                    f"Ham sorgu yükü ({load:.1f} tag×gün) raw eşiğini "
                    f"({raw_max}) aşıyor — dakikalık agregat kullanılıyor."
                ),
            )
        return GuardDecision(allowed=True, forced_aggregate=None, reason="OK")

    if requested_layer == "1min":
        if load > min1_max:
            return GuardDecision(
                allowed=True,
                forced_aggregate="1hour",
                reason=(
                    f"Dakikalık sorgu yükü ({load:.1f} tag×gün) 1min eşiğini "
                    f"({min1_max}) aşıyor — saatlik agregat kullanılıyor."
                ),
            )
        return GuardDecision(allowed=True, forced_aggregate=None, reason="OK")

    # requested_layer == "1hour"
    if time_range_days > hour1_max_days:
        return GuardDecision(
            allowed=False,
            forced_aggregate=None,
            reason=(
                f"Çok uzun pencere ({time_range_days:.0f} gün > "
                f"{hour1_max_days} gün) — parçalı sorgu yapın."
            ),
        )
    return GuardDecision(allowed=True, forced_aggregate=None, reason="OK")
