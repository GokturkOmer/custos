"""Eşik (threshold) değerlendirme çekirdeği — saf, deterministik fonksiyonlar.

Bu modül, kullanıcı-tanımlı alt/üst limit alarmlarının *karar* mantığını
(breach var mı, hysteresis ile temizlenebilir mi, debounce süresi ne) DB ve
süreçten bağımsız saf fonksiyonlar olarak toplar.

Tasarım kararı (31 May 2026 review, H1): eşik tabanlı alarm üretimi Critical
loop'a taşınıyor. Critical loop SADECE ``pymodbus`` + soyut DB arayüzünü
kullanabildiği ve ML/numerik kütüphane import edemediği için, bu karar mantığı
hem Critical (``critical/threshold_watcher.py``) hem Analytics (geçici olarak
``analytics/threshold_engine.py``) tarafından paylaşılabilecek şekilde
``shared/``'a konur — tek kaynak, çoğaltma yok.

Burada SQL/asyncpg YOK; yalnızca ``Threshold`` domain modeli + stdlib.
"""

from __future__ import annotations

from custos.shared.database import Threshold

# Emergency severity için debounce üst sınırı (sn). Yangın/CO/elektrik gibi
# hayati alarmlarda yapılandırılmış debounce ne olursa olsun en fazla bu kadar
# beklenir (V11-107/K10). Not: gerçekleşen gecikme ayrıca tick periyoduna bağlı
# (review M8) — bu yalnızca konfigüre debounce'u kısar.
_EMERGENCY_MAX_DEBOUNCE_SECONDS = 1


def is_breach(threshold: Threshold, value: float) -> bool:
    """Değer eşiği aşıyor mu (breach) kontrol eder.

    ``direction == "high"``: ``value >= set_point`` ise breach.
    ``direction == "low"``:  ``value <= set_point`` ise breach.

    Sınır değeri (tam ``set_point``) breach sayılır; temizleme histerezisi
    (:func:`can_clear_with_hysteresis`) ayrı bir ölü bant uygular, böylece
    set_point civarında flapping önlenir.
    """
    if threshold.direction == "high":
        return value >= threshold.set_point
    # direction == "low"
    return value <= threshold.set_point


def can_clear_with_hysteresis(threshold: Threshold, value: float) -> bool:
    """Aktif alarm hysteresis ölü bandını geçip temizlenebilir mi?

    ``direction == "high"``: ``value < set_point - hysteresis`` ise temizlenir.
    ``direction == "low"``:  ``value > set_point + hysteresis`` ise temizlenir.

    ``hysteresis == 0`` iken tam ``set_point``'te breach sürer (temizlenmez);
    breach (``>=``) ve clear (``<``) asimetrisi kasıtlıdır.
    """
    if threshold.direction == "high":
        return value < threshold.set_point - threshold.hysteresis
    # direction == "low"
    return value > threshold.set_point + threshold.hysteresis


def effective_debounce_seconds(threshold: Threshold) -> int:
    """Bu threshold için fiilen uygulanacak debounce süresini (sn) döndürür.

    Emergency severity'de yapılandırılmış debounce
    ``_EMERGENCY_MAX_DEBOUNCE_SECONDS`` ile sınırlanır (kritik alarm gecikmesini
    kısmak için); diğer severity'lerde ``threshold.debounce_seconds`` aynen
    kullanılır.
    """
    if threshold.severity == "emergency":
        return min(_EMERGENCY_MAX_DEBOUNCE_SECONDS, threshold.debounce_seconds)
    return threshold.debounce_seconds


__all__ = [
    "can_clear_with_hysteresis",
    "effective_debounce_seconds",
    "is_breach",
]
