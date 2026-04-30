"""Stuck-at preset → saniye eşik tablosu (V11-108, P-05).

Liveness engine sensör donma kontrolü için her tag'in ne kadar süre
değişmeden kalabileceğine karar vermeli. K11 hibrit yaklaşımı: pilotun
gün 1'inde çalışacak hardcoded eşik tablosu, ileride (P-12) ML personalize
ile değiştirilecek.

Çözüm akışı (``resolve_stuck_at_seconds``):

1. ``tag.stuck_at_seconds`` doluysa → o değer (manuel override).
2. ``tag.stuck_at_preset == 'auto'`` ise ``tag.unit`` alanından preset
   türet (``UNIT_TO_PRESET``); bilinmeyen birim → 'slow' default.
3. Preset → saniye (``PRESET_SECONDS``).
4. Preset 'none' veya tablo dışı → ``None`` (kontrol kapalı).

K11 kararı: ``°C`` default 'slow' (oda sıcaklığı yaygın); proses suyu
gibi hızlı değişmesi gereken yerlerde tag'da manuel preset='fast' set
edilir.
"""

from __future__ import annotations

from typing import Final

from custos.shared.database import TagRecord

# Preset → eşik saniye. ``counter`` özel: değer; sadece azalma + N sn
# artmama tetikler (``LivenessEngine._check_counter`` mantığı).
PRESET_SECONDS: Final[dict[str, int | None]] = {
    "none": None,           # kontrol kapalı
    "fast": 300,            # 5 dk — basınç, akış, su sıcaklığı
    "slow": 1800,           # 30 dk — oda sıcaklığı, dış hava
    "very_slow": 3600,      # 1 saat — çok durağan değerler
    "counter": 300,         # özel: sayaç artmama eşiği
}

# Birim deseni → preset. ``tag.unit`` alanındaki string ile birebir
# eşleşme aranır; eşleşme yoksa 'slow' default'a düşer (P-05 UNIT_TO_PRESET
# fallback'i).
UNIT_TO_PRESET: Final[dict[str, str]] = {
    "°C": "slow",       # default oda sıcaklığı; proses suyu için override
    "C": "slow",
    "bar": "fast",
    "kPa": "fast",
    "Pa": "fast",
    "mbar": "fast",
    "m³/h": "fast",
    "L/s": "fast",
    "L/min": "fast",
    "Hz": "slow",
    "%": "slow",        # damper / valve pozisyon
    "kWh": "counter",
    "m³": "counter",
    "kW": "fast",
    "A": "fast",
    "V": "slow",
    "rpm": "fast",
    # Status / dijital sinyaller — liveness anlamsiz (FIRE_ALARM, RUNNING,
    # POWER_OK gibi tag'ler tasarim geregi haftalarca degismez, hareket
    # eden sensor degil bilgi sinyali). Bos string ve yaygin boolean
    # birim isimleri 'none' map edilir (kontrol kapali).
    "": "none",
    "bool": "none",
    "boolean": "none",
    "(boolean)": "none",
    "digital": "none",
    "binary": "none",
    "0/1": "none",
    "on/off": "none",
}

# Bilinmeyen birim için fallback preset (`auto` çözümü).
_AUTO_FALLBACK_PRESET: Final[str] = "slow"


def resolve_effective_preset(tag: TagRecord) -> str:
    """Tag'ın etkin preset adını döner ('counter' / 'none' / ...).

    LivenessEngine ``counter`` mantığını ayırt etmek için preset adına
    ihtiyaç duyar (saniyenin yanında). ``stuck_at_seconds`` override
    edilse bile preset adı korunur (counter override edilebilir).
    """
    preset = tag.stuck_at_preset
    if preset == "auto":
        return UNIT_TO_PRESET.get(tag.unit, _AUTO_FALLBACK_PRESET)
    return preset


def resolve_stuck_at_seconds(tag: TagRecord) -> int | None:
    """Tag için aktif stuck-at eşiğini saniye olarak hesaplar.

    Öncelik:

    1. ``tag.stuck_at_seconds`` (manuel override) — ``stuck_at_preset``
       'none' değilse geçerlidir; 'none' her zaman kontrolü kapatır.
    2. ``tag.stuck_at_preset`` ('auto' ise ``tag.unit`` üzerinden
       UNIT_TO_PRESET ile çözülür).
    3. Preset → ``PRESET_SECONDS``.

    Dönüş ``None`` ise kontrol yok (tick atlanmalı).
    """
    if tag.stuck_at_preset == "none":
        return None

    if tag.stuck_at_seconds is not None:
        return tag.stuck_at_seconds

    effective = resolve_effective_preset(tag)
    return PRESET_SECONDS.get(effective)
