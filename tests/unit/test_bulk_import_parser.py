"""Bulk import parser + doğrulama unit testleri.

DB erişimi gerektirmez — yalnızca parse_csv, parse_yaml ve validate_rows
fonksiyonlarını izole test eder.
"""

from __future__ import annotations

import codecs

import pytest

from custos.analytics.dashboard.bulk_import import (
    BulkImportParseError,
    parse_csv,
    parse_file,
    parse_yaml,
    validate_rows,
)

# --- parse_csv ---


def test_parse_csv_happy_path() -> None:
    """10 satır valid CSV → 10 row dict döner."""
    content = (
        b"tag_id,name,modbus_host,register_address,register_type,polling_interval_ms,unit\n"
        + b"\n".join(
            f"TAG_{i:03d},Chiller {i},192.168.1.10,{40001 + i},uint16,1000,degC".encode()
            for i in range(10)
        )
    )
    rows = parse_csv(content)
    assert len(rows) == 10
    assert rows[0]["tag_id"] == "TAG_000"
    assert rows[0]["modbus_host"] == "192.168.1.10"
    assert rows[0]["polling_interval_ms"] == "1000"  # ham string


def test_parse_csv_empty_file() -> None:
    """Boş dosya → boş liste, hata yok."""
    assert parse_csv(b"") == []
    assert parse_csv(b"   \n\n") == []


def test_parse_csv_missing_required_column_raises() -> None:
    """Zorunlu kolon eksik → BulkImportParseError."""
    content = b"name,register_address\nChiller,40001\n"
    with pytest.raises(BulkImportParseError, match="eksik zorunlu kolon"):
        parse_csv(content)


def test_parse_csv_with_bom() -> None:
    """UTF-8 BOM'lu dosya (Excel export) parse edilebilmeli."""
    csv_body = b"tag_id,name,modbus_host,register_address\nCHIL_01,Chiller,10.0.0.1,40001\n"
    content = codecs.BOM_UTF8 + csv_body
    rows = parse_csv(content)
    assert len(rows) == 1
    assert rows[0]["tag_id"] == "CHIL_01"


def test_parse_csv_invalid_utf8_raises() -> None:
    """UTF-8 olmayan bayt dizisi → ParseError."""
    content = b"tag_id,name,modbus_host,register_address\n\xff\xfe,name,host,1\n"
    with pytest.raises(BulkImportParseError, match="UTF-8"):
        parse_csv(content)


def test_parse_csv_blank_cells_become_missing() -> None:
    """Boş hücreler row dict'inden düşer → pydantic default uygulanır."""
    content = b"tag_id,name,modbus_host,register_address,modbus_port\nTAG_A,Test,10.0.0.1,40001,\n"
    rows = parse_csv(content)
    assert len(rows) == 1
    # modbus_port boş → key listede yok
    assert "modbus_port" not in rows[0]


# --- parse_yaml ---


def test_parse_yaml_happy_path_list() -> None:
    """Top-level liste formatı."""
    content = (
        b"- tag_id: TAG_A\n"
        b"  name: Test A\n"
        b"  modbus_host: 10.0.0.1\n"
        b"  register_address: 40001\n"
        b"- tag_id: TAG_B\n"
        b"  name: Test B\n"
        b"  modbus_host: 10.0.0.1\n"
        b"  register_address: 40002\n"
    )
    rows = parse_yaml(content)
    assert len(rows) == 2
    assert rows[1]["tag_id"] == "TAG_B"


def test_parse_yaml_happy_path_tags_key() -> None:
    """`tags: [...]` sarması."""
    content = (
        b"tags:\n"
        b"  - tag_id: TAG_A\n"
        b"    name: Test A\n"
        b"    modbus_host: 10.0.0.1\n"
        b"    register_address: 40001\n"
    )
    rows = parse_yaml(content)
    assert len(rows) == 1
    assert rows[0]["tag_id"] == "TAG_A"


def test_parse_yaml_empty_file() -> None:
    """Boş YAML → boş liste."""
    assert parse_yaml(b"") == []
    assert parse_yaml(b"\n\n") == []


def test_parse_yaml_malformed_raises() -> None:
    """Bozuk YAML → ParseError."""
    content = b"tags: [unclosed\n"
    with pytest.raises(BulkImportParseError, match="YAML parse hatası"):
        parse_yaml(content)


def test_parse_yaml_top_level_scalar_raises() -> None:
    """YAML'de liste beklerken skalar → ParseError."""
    content = b"just a string\n"
    with pytest.raises(BulkImportParseError, match="liste olmalı"):
        parse_yaml(content)


# --- parse_file (uzantı yönlendirmesi) ---


def test_parse_file_csv_extension() -> None:
    rows = parse_file(
        "tags.csv",
        b"tag_id,name,modbus_host,register_address\nA,B,C,40001\n",
    )
    assert len(rows) == 1


def test_parse_file_yaml_extension() -> None:
    content = b"- tag_id: A\n  name: B\n  modbus_host: C\n  register_address: 40001\n"
    rows = parse_file("tags.yaml", content)
    assert len(rows) == 1


def test_parse_file_unknown_extension_raises() -> None:
    with pytest.raises(BulkImportParseError, match="Desteklenmeyen"):
        parse_file("tags.xlsx", b"")


# --- validate_rows ---


def _valid_row(tag_id: str = "TAG_01") -> dict[str, object]:
    return {
        "tag_id": tag_id,
        "name": f"Test {tag_id}",
        "modbus_host": "10.0.0.1",
        "register_address": 40001,
    }


def test_validate_rows_all_valid() -> None:
    """10 valid row → preview.valid=10, errors=0."""
    raw = [_valid_row(f"TAG_{i:03d}") for i in range(10)]
    result = validate_rows(raw)
    assert len(result.valid) == 10
    assert result.errors == []
    assert result.ok is True


def test_validate_rows_invalid_register_type() -> None:
    row = _valid_row()
    row["register_type"] = "uint8"  # izinli değil
    result = validate_rows([row])
    assert not result.ok
    assert len(result.errors) == 1
    assert result.errors[0].field == "register_type"
    assert result.errors[0].row_num == 2  # ilk data satırı = 2


def test_validate_rows_invalid_polling_interval() -> None:
    row = _valid_row()
    row["polling_interval_ms"] = 500  # fast/normal/slow dışı
    result = validate_rows([row])
    assert not result.ok
    assert result.errors[0].field == "polling_interval_ms"


def test_validate_rows_out_of_range_port() -> None:
    row = _valid_row()
    row["modbus_port"] = 70000
    result = validate_rows([row])
    assert not result.ok
    assert result.errors[0].field == "modbus_port"


def test_validate_rows_out_of_range_unit_id() -> None:
    row = _valid_row()
    row["unit_id"] = 300  # Modbus limiti 247
    result = validate_rows([row])
    assert not result.ok
    assert result.errors[0].field == "unit_id"


def test_validate_rows_out_of_range_register_address() -> None:
    row = _valid_row()
    row["register_address"] = 100000
    result = validate_rows([row])
    assert not result.ok
    assert result.errors[0].field == "register_address"


def test_validate_rows_duplicate_tag_id_same_file() -> None:
    """Aynı dosyada iki kez aynı tag_id → ikincisi hata, ilki geçerli."""
    rows = [_valid_row("TAG_DUPE"), _valid_row("TAG_DUPE")]
    result = validate_rows(rows)
    assert len(result.valid) == 1
    assert len(result.errors) == 1
    assert result.errors[0].field == "tag_id"
    assert "tekrar eden" in result.errors[0].message
    # İkinci satır (row_num 3) hata alır
    assert result.errors[0].row_num == 3


def test_validate_rows_missing_required_field() -> None:
    """Zorunlu alan eksik → pydantic missing errors."""
    rows = [{"tag_id": "A", "name": "B"}]  # modbus_host + register_address yok
    result = validate_rows(rows)
    assert not result.ok
    fields = {e.field for e in result.errors}
    assert "modbus_host" in fields
    assert "register_address" in fields


def test_validate_rows_to_tag_record_conversion() -> None:
    """BulkImportRow.to_tag_record → 40001+ adres 0-based'a çevrilir."""
    rows = [_valid_row()]
    rows[0]["register_address"] = 40042
    rows[0]["polling_interval_ms"] = 1000
    result = validate_rows(rows)
    assert len(result.valid) == 1
    _, row = result.valid[0]
    tag = row.to_tag_record()
    assert tag.register_address == 41  # 40042 - 40001
    assert tag.polling_preset == "normal"


def test_validate_rows_polling_preset_derivation() -> None:
    """polling_interval_ms → polling_preset doğru eşleşmeli."""
    for interval, expected_preset in [(100, "fast"), (1000, "normal"), (10000, "slow")]:
        row = _valid_row(f"TAG_{interval}")
        row["polling_interval_ms"] = interval
        result = validate_rows([row])
        assert len(result.valid) == 1, f"interval {interval} geçerli olmalı"
        _, parsed = result.valid[0]
        assert parsed.to_tag_record().polling_preset == expected_preset


def test_validate_rows_empty_list() -> None:
    """Boş input → boş sonuç, hata yok."""
    result = validate_rows([])
    assert result.valid == []
    assert result.errors == []
    assert result.ok is True


def test_validate_rows_string_register_type_normalized() -> None:
    """'UINT16' gibi büyük harfli input lowercase'e normalize edilmeli."""
    row = _valid_row()
    row["register_type"] = "UINT16"
    result = validate_rows([row])
    assert result.ok
    _, parsed = result.valid[0]
    assert parsed.register_type == "uint16"
