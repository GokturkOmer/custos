"""Register decoder unit testleri (F11 Paket I).

Kapsam:
    - uint16/int16: pozitif, negatif, sınır değer
    - uint32/int32: big/little byte_order
    - float32: IEEE 754, big/little byte_order, özel değerler
    - gain + offset uygulaması
    - Hata yolları: desteklenmeyen tip, yanlış sayıda register,
      geçersiz byte_order

Test çoğunlukla saf hesaplama — DB veya Modbus gerektirmez.
"""

from __future__ import annotations

import math
import struct

import pytest

from custos.critical.register_decoder import (
    SUPPORTED_REGISTER_TYPES,
    RegisterDecodeError,
    decode_register,
    expected_word_count,
)
from custos.shared.database import TagRecord


def _tag(
    register_type: str,
    *,
    byte_order: str = "big",
    gain: float = 1.0,
    offset: float = 0.0,
) -> TagRecord:
    """Test için minimum TagRecord üretir."""
    return TagRecord(
        tag_id="TEST",
        name="test",
        modbus_host="10.0.0.1",
        modbus_port=502,
        unit_id=1,
        register_address=0,
        register_type=register_type,
        byte_order=byte_order,
        gain=gain,
        offset=offset,
        unit="",
        polling_interval_ms=1000,
        polling_preset="normal",
    )


# --- expected_word_count ---


def test_expected_word_count_all_types() -> None:
    """Her desteklenen tip için doğru register sayısı dönmeli."""
    assert expected_word_count("uint16") == 1
    assert expected_word_count("int16") == 1
    assert expected_word_count("uint32") == 2
    assert expected_word_count("int32") == 2
    assert expected_word_count("float32") == 2


def test_expected_word_count_unsupported_raises() -> None:
    """Desteklenmeyen tipte RegisterDecodeError."""
    with pytest.raises(RegisterDecodeError, match="Desteklenmeyen"):
        expected_word_count("uint64")


def test_supported_types_set_is_frozen() -> None:
    """SUPPORTED_REGISTER_TYPES frozenset ve beklenen set'i içerir."""
    assert isinstance(SUPPORTED_REGISTER_TYPES, frozenset)
    assert SUPPORTED_REGISTER_TYPES == {
        "uint16",
        "int16",
        "uint32",
        "int32",
        "float32",
    }


# --- uint16 ---


def test_uint16_zero() -> None:
    assert decode_register((0,), _tag("uint16")) == 0.0


def test_uint16_max() -> None:
    assert decode_register((0xFFFF,), _tag("uint16")) == 65535.0


def test_uint16_mid() -> None:
    assert decode_register((0x4000,), _tag("uint16")) == 16384.0


def test_uint16_with_gain_offset() -> None:
    """gain=0.1, offset=-40 -> sıcaklık ölçeği örneği."""
    # raw=400 -> 400 * 0.1 + (-40) = 0
    assert decode_register((400,), _tag("uint16", gain=0.1, offset=-40.0)) == pytest.approx(0.0)


# --- int16 ---


def test_int16_positive() -> None:
    assert decode_register((1234,), _tag("int16")) == 1234.0


def test_int16_negative() -> None:
    """0xFFFF = -1 signed."""
    assert decode_register((0xFFFF,), _tag("int16")) == -1.0


def test_int16_min_boundary() -> None:
    """0x8000 = -32768 (signed min)."""
    assert decode_register((0x8000,), _tag("int16")) == -32768.0


def test_int16_max_boundary() -> None:
    """0x7FFF = 32767 (signed max)."""
    assert decode_register((0x7FFF,), _tag("int16")) == 32767.0


# --- uint32 ---


def test_uint32_big_word_order() -> None:
    """big (AB-CD): registers=(hi, lo). 0x0001_0002 -> (0x0001, 0x0002)."""
    assert decode_register((0x0001, 0x0002), _tag("uint32", byte_order="big")) == float(0x00010002)


def test_uint32_little_word_order() -> None:
    """little (CD-AB): registers=(lo, hi). Aynı değer için ters sıra."""
    # 0x0001_0002 = 65538 -> little word order'da (lo=0x0002, hi=0x0001)
    assert decode_register((0x0002, 0x0001), _tag("uint32", byte_order="little")) == 65538.0


def test_uint32_max() -> None:
    """0xFFFFFFFF = 4294967295."""
    assert decode_register((0xFFFF, 0xFFFF), _tag("uint32", byte_order="big")) == 4294967295.0


def test_uint32_zero() -> None:
    assert decode_register((0, 0), _tag("uint32", byte_order="big")) == 0.0


# --- int32 ---


def test_int32_positive_big() -> None:
    assert decode_register((0x0001, 0x0000), _tag("int32", byte_order="big")) == 65536.0


def test_int32_negative_big() -> None:
    """-1 = 0xFFFFFFFF."""
    assert decode_register((0xFFFF, 0xFFFF), _tag("int32", byte_order="big")) == -1.0


def test_int32_min_boundary() -> None:
    """0x80000000 = -2147483648."""
    assert decode_register((0x8000, 0x0000), _tag("int32", byte_order="big")) == -2147483648.0


def test_int32_max_boundary() -> None:
    """0x7FFFFFFF = 2147483647."""
    assert decode_register((0x7FFF, 0xFFFF), _tag("int32", byte_order="big")) == 2147483647.0


def test_int32_little_word_order() -> None:
    """little word order -1: (lo=0xFFFF, hi=0xFFFF) zaten simetrik."""
    assert decode_register((0xFFFF, 0xFFFF), _tag("int32", byte_order="little")) == -1.0

    # Asimetrik örnek: 0x0001_0000 big = 65536. little word order'da registers
    # (lo=0x0000, hi=0x0001).
    assert decode_register((0x0000, 0x0001), _tag("int32", byte_order="little")) == 65536.0


# --- float32 ---


def test_float32_one_big() -> None:
    """1.0 = 0x3F800000. Big word order: (0x3F80, 0x0000)."""
    result = decode_register((0x3F80, 0x0000), _tag("float32", byte_order="big"))
    assert result == pytest.approx(1.0, abs=1e-5)


def test_float32_negative() -> None:
    """-1.5 = 0xBFC00000."""
    result = decode_register((0xBFC0, 0x0000), _tag("float32", byte_order="big"))
    assert result == pytest.approx(-1.5, abs=1e-5)


def test_float32_pi() -> None:
    """Pi'yi pack+decode et, round-trip doğruluğu."""
    packed = struct.pack(">f", math.pi)
    hi, lo = struct.unpack(">HH", packed)
    result = decode_register((hi, lo), _tag("float32", byte_order="big"))
    assert result == pytest.approx(math.pi, rel=1e-6)


def test_float32_little_word_order() -> None:
    """Little word order: registers ters sırada verilir."""
    # 1.0 big: (0x3F80, 0x0000). Little: (0x0000, 0x3F80)
    result = decode_register((0x0000, 0x3F80), _tag("float32", byte_order="little"))
    assert result == pytest.approx(1.0, abs=1e-5)


def test_float32_zero() -> None:
    assert decode_register((0x0000, 0x0000), _tag("float32", byte_order="big")) == 0.0


def test_float32_with_gain_offset() -> None:
    """float32 * gain + offset uygulanır."""
    # 2.0 * 0.5 + 1 = 2.0
    result = decode_register(
        (0x4000, 0x0000), _tag("float32", byte_order="big", gain=0.5, offset=1.0)
    )
    assert result == pytest.approx(2.0, abs=1e-5)


# --- Hata yolları ---


def test_unsupported_register_type_raises() -> None:
    """decode_register desteklenmeyen tipte hata atar."""
    with pytest.raises(RegisterDecodeError, match="Desteklenmeyen register_type"):
        decode_register((0,), _tag("uint64"))


def test_wrong_register_count_uint16() -> None:
    """uint16 1 register bekler, 2 verilirse hata."""
    with pytest.raises(RegisterDecodeError, match="1 register bekleniyor"):
        decode_register((0, 0), _tag("uint16"))


def test_wrong_register_count_uint32() -> None:
    """uint32 2 register bekler, 1 verilirse hata."""
    with pytest.raises(RegisterDecodeError, match="2 register bekleniyor"):
        decode_register((0,), _tag("uint32"))


def test_invalid_byte_order_raises() -> None:
    """byte_order='middle' gibi bilinmeyen değer hata atar."""
    with pytest.raises(RegisterDecodeError, match="Desteklenmeyen byte_order"):
        decode_register((0, 0), _tag("uint32", byte_order="middle"))
