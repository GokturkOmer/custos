"""Modbus register type decoder (F11 Paket I).

Batch okunan register listesinden tek bir tag için değer üretir.
Desteklenen tipler: uint16, int16, uint32, int32, float32.

Word order (byte_order TagRecord alanı, Modbus geleneğinde "word order"):
    - "big" (AB-CD): yüksek word ilk register'da (hi, lo) — IEEE 754
      standart sırası, çoğu PLC varsayılanı.
    - "little" (CD-AB): düşük word ilk register'da (lo, hi) —
      bazı Schneider / Danfoss modelleri.

uint16/int16 için word order önemsiz (tek register).

gain + offset her tipe uygulanır: ``value = raw * gain + offset``.

Mimari kural (CLAUDE.md): Bu modül critical loop'ta olduğu için sadece
stdlib ve TagRecord kullanır. asyncpg/SQL/ORM YASAK.
"""

from __future__ import annotations

import struct

from custos.shared.database import TagRecord

# Her tip için beklenen register sayısı.
_WORD_COUNT: dict[str, int] = {
    "uint16": 1,
    "int16": 1,
    "uint32": 2,
    "int32": 2,
    "float32": 2,
}

# Desteklenen tipler (hata mesajı ve validation için).
SUPPORTED_REGISTER_TYPES = frozenset(_WORD_COUNT.keys())


class RegisterDecodeError(ValueError):
    """Register decode başarısız olduğunda atılır.

    Muhtemel sebepler: desteklenmeyen register_type, yanlış sayıda
    register, geçersiz byte_order değeri.
    """


def expected_word_count(register_type: str) -> int:
    """Bu tip için kaç register beklenir döndürür.

    Desteklenmeyen tip -> RegisterDecodeError.
    """
    if register_type not in _WORD_COUNT:
        msg = (
            f"Desteklenmeyen register_type: {register_type!r}. "
            f"Desteklenenler: {sorted(SUPPORTED_REGISTER_TYPES)}"
        )
        raise RegisterDecodeError(msg)
    return _WORD_COUNT[register_type]


def _combine_words(raw_values: tuple[int, ...], byte_order: str) -> bytes:
    """İki 16-bit register'ı 32-bit byte dizisine çevirir.

    byte_order="big": (hi, lo) -> 4 byte BE. Python struct '>HH' ile
    (hi, lo) yazmak doğru sonucu verir: hi yüksek 16 bit, lo düşük 16 bit.

    byte_order="little": (lo, hi) kabul eder; 4 byte BE formatına
    çevirmek için argümanları takla atıp '>HH' ile yazarız.
    """
    if byte_order == "big":
        hi, lo = raw_values[0], raw_values[1]
    elif byte_order == "little":
        hi, lo = raw_values[1], raw_values[0]
    else:
        msg = (
            f"Desteklenmeyen byte_order: {byte_order!r}. "
            f"'big' veya 'little' bekleniyor."
        )
        raise RegisterDecodeError(msg)
    return struct.pack(">HH", hi, lo)


def decode_register(raw_values: tuple[int, ...], tag: TagRecord) -> float:
    """Register listesinden tag için fiziksel değer üretir.

    Args:
        raw_values: Batch okumadan kesilmiş, bu tag için 1 veya 2 register.
        tag: Hedef tag (register_type, byte_order, gain, offset belirler).

    Returns:
        Fiziksel değer: raw * gain + offset.

    Raises:
        RegisterDecodeError: Desteklenmeyen tip, yanlış register sayısı,
            geçersiz byte_order.
    """
    expected = expected_word_count(tag.register_type)
    if len(raw_values) != expected:
        msg = (
            f"Tag {tag.tag_id} için {expected} register bekleniyor, "
            f"{len(raw_values)} alındı (register_type={tag.register_type})"
        )
        raise RegisterDecodeError(msg)

    # 1 register tipleri: uint16 / int16
    if tag.register_type == "uint16":
        raw_float: float = float(raw_values[0])
    elif tag.register_type == "int16":
        # Signed 16-bit interpretasyonu
        u = raw_values[0]
        signed = u - 0x10000 if u >= 0x8000 else u
        raw_float = float(signed)
    else:
        # 2 register tipleri: uint32 / int32 / float32
        packed = _combine_words(raw_values, tag.byte_order)
        if tag.register_type == "uint32":
            raw_float = float(struct.unpack(">I", packed)[0])
        elif tag.register_type == "int32":
            raw_float = float(struct.unpack(">i", packed)[0])
        else:
            # float32
            raw_float = float(struct.unpack(">f", packed)[0])

    return raw_float * tag.gain + tag.offset


__all__ = [
    "SUPPORTED_REGISTER_TYPES",
    "RegisterDecodeError",
    "decode_register",
    "expected_word_count",
]
