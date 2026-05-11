"""CSV replay simulator unit testleri (Faz 1.2).

Modbus veya PG erisimi gerektirmez — saf encode/parse/filter fonksiyonlarini
ve build_register_block paketleyicisini izole test eder. End-to-end
replay_csv() ayri integration test'te (gerekirse) kapsanir.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


def _load_simulator_module() -> ModuleType:
    """scripts/csv_replay_simulator.py'yi path-by-path yukle.

    scripts/ Python paketi degil; importlib ile dosya yolundan yukle.
    Modul ``sys.modules``'a kaydedilir — dataclass tarafindan gerekli
    (dataclass class lookup'i sys.modules'tan yapilir, aksi halde
    AttributeError firlatir).
    """
    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts" / "csv_replay_simulator.py"
    module_name = "csv_replay_simulator"
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    if spec is None or spec.loader is None:
        msg = f"Simulator modulu yuklenemedi: {script_path}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


sim = _load_simulator_module()


# --- encode_value tests ---


def test_encode_uint16_round_trip() -> None:
    """gain=0.01 offset=0 ile 35.27 °C → raw 3527."""
    entry = sim.TagEntry(
        sensor_name="t",
        custos_tag_name="t",
        register_address=1000,
        register_type="uint16",
        gain=0.01,
        offset=0.0,
    )
    assert sim.encode_value(35.27, entry) == [3527]


def test_encode_uint16_clamps_high() -> None:
    """uint16 ust limit 0xFFFF asarsa clamp edilir (raw sahasi korunur)."""
    entry = sim.TagEntry(
        sensor_name="t",
        custos_tag_name="t",
        register_address=1000,
        register_type="uint16",
        gain=1.0,
        offset=0.0,
    )
    # 100000 > 65535 → clamp
    assert sim.encode_value(100000.0, entry) == [0xFFFF]


def test_encode_int16_negative_two_complement() -> None:
    """int16 negatif → uint16 two's complement: -1 → 0xFFFF, -100 → 0xFF9C."""
    entry = sim.TagEntry(
        sensor_name="t",
        custos_tag_name="t",
        register_address=1000,
        register_type="int16",
        gain=1.0,
        offset=0.0,
    )
    assert sim.encode_value(-1.0, entry) == [0xFFFF]
    assert sim.encode_value(-100.0, entry) == [0xFF9C]
    assert sim.encode_value(1.0, entry) == [0x0001]


def test_encode_int16_with_gain() -> None:
    """gain=0.01, offset=0, value=-5.0 → raw -500 → uint16 0xFE0C."""
    entry = sim.TagEntry(
        sensor_name="t",
        custos_tag_name="t",
        register_address=1000,
        register_type="int16",
        gain=0.01,
        offset=0.0,
    )
    # -5.0 / 0.01 = -500 → two's complement uint16
    # -500 & 0xFFFF = 65036 = 0xFE0C
    assert sim.encode_value(-5.0, entry) == [0xFE0C]


def test_encode_uint32_big_word_order() -> None:
    """uint32: 0x12345678 → [0x1234, 0x5678] (hi word once — register_decoder default)."""
    entry = sim.TagEntry(
        sensor_name="t",
        custos_tag_name="t",
        register_address=1000,
        register_type="uint32",
        gain=1.0,
        offset=0.0,
    )
    assert sim.encode_value(float(0x12345678), entry) == [0x1234, 0x5678]


def test_encode_int32_negative_round_trip() -> None:
    """int32: -1 → 0xFFFFFFFF → [0xFFFF, 0xFFFF]."""
    entry = sim.TagEntry(
        sensor_name="t",
        custos_tag_name="t",
        register_address=1000,
        register_type="int32",
        gain=1.0,
        offset=0.0,
    )
    assert sim.encode_value(-1.0, entry) == [0xFFFF, 0xFFFF]


def test_encode_nan_returns_zero_words() -> None:
    """NaN deger 0 dolduran word listesi dondurur, uyari log'lar."""
    entry_uint16 = sim.TagEntry(
        sensor_name="t",
        custos_tag_name="t",
        register_address=1000,
        register_type="uint16",
        gain=1.0,
        offset=0.0,
    )
    entry_int32 = sim.TagEntry(
        sensor_name="t",
        custos_tag_name="t",
        register_address=1000,
        register_type="int32",
        gain=1.0,
        offset=0.0,
    )
    assert sim.encode_value(float("nan"), entry_uint16) == [0]
    assert sim.encode_value(float("nan"), entry_int32) == [0, 0]


# --- load_tag_map tests ---


def test_load_tag_map_parses_basic_csv(tmp_path: Path) -> None:
    """5 satirlik sentetik tag map → 5 TagEntry."""
    tag_csv = tmp_path / "tag_map.csv"
    tag_csv.write_text(
        "sensor_name,custos_tag_name,description,unit,"
        "register_address,register_type,is_angle,is_counter,gain,offset,"
        "default_threshold_low,default_threshold_high,notes\n"
        "sensor_0_avg,t_amb,Amb,C,1000,int16,false,false,0.01,0.0,,,\n"
        "sensor_1_avg,t_wind_dir,Dir,deg,1001,int16,true,false,0.01,0.0,,,\n"
        "wind_speed_3_avg,ws_avg,WS,m/s,1003,uint16,false,false,0.01,0.0,,,\n"
        "power_30_avg,p_avg,P,kW,1060,int32,false,false,0.001,0.0,,,\n"
        "sensor_50,t_active_energy,E,Wh,1100,uint32,false,false,1.0,0.0,,,\n",
        encoding="utf-8",
    )
    entries = sim.load_tag_map(tag_csv)
    assert len(entries) == 5
    assert entries[0].sensor_name == "sensor_0_avg"
    assert entries[0].register_type == "int16"
    assert entries[0].word_count == 1
    assert entries[3].register_type == "int32"
    assert entries[3].word_count == 2
    assert entries[4].register_type == "uint32"
    assert entries[4].register_address == 1100


def test_load_tag_map_skips_unsupported_register_type(
    tmp_path: Path,
    caplog: Any,
) -> None:
    """Desteklenmeyen tip (float32) atlanir, uyari log'lar."""
    tag_csv = tmp_path / "tag_map.csv"
    tag_csv.write_text(
        "sensor_name,custos_tag_name,description,unit,"
        "register_address,register_type,is_angle,is_counter,gain,offset,"
        "default_threshold_low,default_threshold_high,notes\n"
        "ok_tag,t1,Ok,C,1000,uint16,false,false,1.0,0.0,,,\n"
        "bad_tag,t2,Bad,C,1001,float32,false,false,1.0,0.0,,,\n",
        encoding="utf-8",
    )
    entries = sim.load_tag_map(tag_csv)
    assert len(entries) == 1
    assert entries[0].sensor_name == "ok_tag"


# --- load_event_info tests ---


def test_load_event_info_semicolon_csv(tmp_path: Path) -> None:
    """event_info.csv (semicolon-separated) parse + EventInfo eslesir."""
    path = tmp_path / "event_info.csv"
    path.write_text(
        "asset;event_id;event_label;event_start;event_start_id;"
        "event_end;event_end_id;event_description\n"
        "0;0;anomaly;2023-08-06 06:10:00;52436;"
        "2023-08-20 06:10:00;54447;Generator bearing failure\n"
        "0;24;normal;2023-04-27 15:00:00;52720;"
        "2023-05-11 11:20:00;54714;\n",
        encoding="utf-8",
    )
    events = sim.load_event_info(path)
    assert 0 in events
    assert 24 in events
    assert events[0].event_label == "anomaly"
    assert events[0].event_start_id == 52436
    assert events[0].event_end_id == 54447
    assert events[0].event_description == "Generator bearing failure"
    assert events[24].event_description == ""  # bos hucre korunur


# --- parse_status_filter tests ---


def test_parse_status_filter_comma_list() -> None:
    """`'0,2'` → {0, 2}."""
    assert sim.parse_status_filter("0,2") == frozenset({0, 2})


def test_parse_status_filter_range() -> None:
    """`'0-2'` → {0, 1, 2}."""
    assert sim.parse_status_filter("0-2") == frozenset({0, 1, 2})


def test_parse_status_filter_mixed() -> None:
    """`'0-1,4'` → {0, 1, 4}."""
    assert sim.parse_status_filter("0-1,4") == frozenset({0, 1, 4})


def test_parse_status_filter_none_returns_none() -> None:
    """None ve bos string → hicbir filtre (replay tum status'lari)."""
    assert sim.parse_status_filter(None) is None
    assert sim.parse_status_filter("") is None


def test_parse_status_filter_invalid_raises() -> None:
    """Gecersiz id (6) → ValueError."""
    with pytest.raises(ValueError, match="Gecersiz status_type_id"):
        sim.parse_status_filter("0,6")


# --- build_register_block tests ---


def test_build_register_block_packs_contiguous_block() -> None:
    """3 tag → tek block. Kullanilmayan offset 0 ile dolu."""
    entries = [
        sim.TagEntry("a", "t_a", 1000, "uint16", 0.1, 0.0),  # 1000 (1 word)
        sim.TagEntry("b", "t_b", 1002, "int32", 0.001, 0.0),  # 1002-1003 (2 word)
        sim.TagEntry("c", "t_c", 1004, "int16", 1.0, 0.0),  # 1004 (1 word)
    ]
    # a=10.0 → raw 100 → [100]
    # b=1234.567 → raw 1234567 → 0x0012D687 → [0x0012, 0xD687]
    # c=-1 → 0xFFFF
    row = {"a": "10.0", "b": "1234.567", "c": "-1.0"}
    base, block = sim.build_register_block(row, entries)
    assert base == 1000
    # 1001 doldurma 0 (tag yok)
    assert block == [100, 0, 0x0012, 0xD687, 0xFFFF]


def test_build_register_block_nan_value_writes_zero() -> None:
    """Bos/nan deger 0 ile yazilir (collector tarafinda null reading)."""
    entries = [
        sim.TagEntry("a", "t_a", 1000, "uint16", 1.0, 0.0),
        sim.TagEntry("b", "t_b", 1001, "int32", 1.0, 0.0),
    ]
    row = {"a": "", "b": "nan"}
    base, block = sim.build_register_block(row, entries)
    assert base == 1000
    assert block == [0, 0, 0]


# --- ground truth log tests ---


def test_ground_truth_log_format(caplog: Any) -> None:
    """tick_id event_start_id'e esit → STARTED log'lari (idempotent)."""
    events = {
        0: sim.EventInfo(
            asset="0",
            event_id=0,
            event_label="anomaly",
            event_start_id=52436,
            event_end_id=54447,
            event_description="Generator bearing failure",
        ),
    }
    seen_starts: set[int] = set()
    seen_ends: set[int] = set()
    with caplog.at_level(logging.INFO, logger="csv_replay_simulator"):
        sim._log_ground_truth(52436, events, seen_starts, seen_ends)
        sim._log_ground_truth(52436, events, seen_starts, seen_ends)  # idempotent
        sim._log_ground_truth(54447, events, seen_starts, seen_ends)

    started_msgs = [r for r in caplog.records if "STARTED" in r.getMessage()]
    ended_msgs = [r for r in caplog.records if "ENDED" in r.getMessage()]
    assert len(started_msgs) == 1, "Idempotent: ayni event icin sadece 1 STARTED"
    assert len(ended_msgs) == 1
    assert "Generator bearing failure" in started_msgs[0].getMessage()
    assert "csv_id=52436" in started_msgs[0].getMessage()


# --- speed timing (saf hesap, end-to-end yok) ---


def test_speed_argument_computes_sleep_per_tick() -> None:
    """speed=1000 → tick suresi 10*60/1000 = 0.6 sn (gercekleme aritmetigi)."""
    speed = 1000.0
    sleep_per_tick = sim.TICK_SECONDS_REAL / speed
    # 5 tick toplam < 5 saniye olmali (3 sn beklenir)
    assert sleep_per_tick == 0.6
    assert sleep_per_tick * 5 < 5.0


# --- argparser smoke ---


def test_argparser_help_renders_without_error() -> None:
    """--help kurulu — gerekli arg'lar reddedildiginde error olmamali."""
    parser = sim._build_argparser()
    # Help mesaji crash etmeden render olmali (tek satirda).
    help_text = parser.format_help()
    assert "--csv" in help_text
    assert "--tag-map" in help_text
    assert "--asset-instance-id" in help_text
    assert "--speed" in help_text
    assert "--status-filter" in help_text
