"""Endurance test için 200 tag'lik AVM HVAC anlamlı CSV üretir.

Saf B (Prompt 14) endurance senaryosu için tasarlandı. 200 tag, 5 ekipman
ailesine yayılmış, gerçekçi AVM (alışveriş merkezi) HVAC izlenir:
    - 4× AHU (Air Handling Unit)
    - 2× Chiller (soğutma)
    - 8× FCU (Fan Coil Unit)
    - 12× Sirkülasyon pompası
    - 2× Booster pompası
    - 2× Cooling tower
    - 2× Asansör + güç sistemi + güvenlik

Register layout (drift_simulator_diagslave.py ile birebir uyumlu):
    Reg 0-49    Sıcaklık     gain=0.1, raw 200-250 → 20-25 °C
    Reg 50-99   Basınç       gain=0.01, raw 100-1000 → 1-10 bar
    Reg 100-149 Enerji       gain=1.0, monoton kWh sayacı
    Reg 150-179 RPM          gain=1.0, raw 1000-2000
    Reg 180-199 Status       gain=1.0, 0/1 boolean

CSV Şeması (bulk_import.BulkImportRow uyumlu):
    tag_id,name,modbus_host,modbus_port,unit_id,register_address,
    register_type,byte_order,gain,offset,unit,polling_interval_ms

`tag_id` İngilizce sektör standardı (kod, threshold, KPI formülünde).
`name`  Türkçe (dashboard, alarm UI, demo görsel).

Kullanım:
    python scripts/endurance_generate_avm_tags_csv.py
    python scripts/endurance_generate_avm_tags_csv.py --host 127.0.0.1 --port 502
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import NamedTuple

# Modbus holding register protokol → 40001 base konvansiyonu
# (bulk_import.py 40001 öncesi protokol adresi olarak indirger)
_MODBUS_HOLDING_BASE = 40001

# Polling preset eşikleri (1-tabanlı CSV satır indeksi)
_POLLING_SLOW_MAX = 150    # 1..150 → slow 10000 ms (temp + pres + energy)
_POLLING_NORMAL_MAX = 195  # 151..195 → normal 1000 ms (RPM + ilk 15 status)
# 196..200 → fast 100 ms (son 5 status)


class TagSpec(NamedTuple):
    """Tek bir endurance tag'inin tüm meta bilgisi."""

    tag_id: str
    name: str
    register: int        # 0-tabanlı PDU offset
    unit: str
    gain: float


# ============================================================================
# SICAKLIK GRUBU (50 tag, register 0-49, gain 0.1, °C)
# ============================================================================
TEMP_TAGS: list[TagSpec] = [
    # AHU 01-04 — her biri 5 sıcaklık (20 tag)
    TagSpec("AHU01_SUPPLY_TEMP",       "AHU-1 Üfleme Havası Sıcaklığı",          0, "°C", 0.1),
    TagSpec("AHU01_RETURN_TEMP",       "AHU-1 Dönüş Havası Sıcaklığı",           1, "°C", 0.1),
    TagSpec("AHU01_OUTSIDE_AIR_TEMP",  "AHU-1 Dış Hava Sıcaklığı",               2, "°C", 0.1),
    TagSpec("AHU01_MIXED_AIR_TEMP",    "AHU-1 Karışım Hava Sıcaklığı",           3, "°C", 0.1),
    TagSpec("AHU01_HEATING_COIL_TEMP", "AHU-1 Isıtma Bataryası Sıcaklığı",       4, "°C", 0.1),
    TagSpec("AHU02_SUPPLY_TEMP",       "AHU-2 Üfleme Havası Sıcaklığı",          5, "°C", 0.1),
    TagSpec("AHU02_RETURN_TEMP",       "AHU-2 Dönüş Havası Sıcaklığı",           6, "°C", 0.1),
    TagSpec("AHU02_OUTSIDE_AIR_TEMP",  "AHU-2 Dış Hava Sıcaklığı",               7, "°C", 0.1),
    TagSpec("AHU02_MIXED_AIR_TEMP",    "AHU-2 Karışım Hava Sıcaklığı",           8, "°C", 0.1),
    TagSpec("AHU02_HEATING_COIL_TEMP", "AHU-2 Isıtma Bataryası Sıcaklığı",       9, "°C", 0.1),
    TagSpec("AHU03_SUPPLY_TEMP",       "AHU-3 Üfleme Havası Sıcaklığı",         10, "°C", 0.1),
    TagSpec("AHU03_RETURN_TEMP",       "AHU-3 Dönüş Havası Sıcaklığı",          11, "°C", 0.1),
    TagSpec("AHU03_OUTSIDE_AIR_TEMP",  "AHU-3 Dış Hava Sıcaklığı",              12, "°C", 0.1),
    TagSpec("AHU03_MIXED_AIR_TEMP",    "AHU-3 Karışım Hava Sıcaklığı",          13, "°C", 0.1),
    TagSpec("AHU03_HEATING_COIL_TEMP", "AHU-3 Isıtma Bataryası Sıcaklığı",      14, "°C", 0.1),
    TagSpec("AHU04_SUPPLY_TEMP",       "AHU-4 Üfleme Havası Sıcaklığı",         15, "°C", 0.1),
    TagSpec("AHU04_RETURN_TEMP",       "AHU-4 Dönüş Havası Sıcaklığı",          16, "°C", 0.1),
    TagSpec("AHU04_OUTSIDE_AIR_TEMP",  "AHU-4 Dış Hava Sıcaklığı",              17, "°C", 0.1),
    TagSpec("AHU04_MIXED_AIR_TEMP",    "AHU-4 Karışım Hava Sıcaklığı",          18, "°C", 0.1),
    TagSpec("AHU04_HEATING_COIL_TEMP", "AHU-4 Isıtma Bataryası Sıcaklığı",      19, "°C", 0.1),
    # Chiller 01-02 — her biri 5 sıcaklık (10 tag)
    TagSpec("CHILLER01_EVAP_IN_TEMP",  "Chiller-1 Evaporatör Giriş Sıcaklığı",  20, "°C", 0.1),
    TagSpec("CHILLER01_EVAP_OUT_TEMP", "Chiller-1 Evaporatör Çıkış Sıcaklığı",  21, "°C", 0.1),
    TagSpec("CHILLER01_COND_IN_TEMP",  "Chiller-1 Kondenser Giriş Sıcaklığı",   22, "°C", 0.1),
    TagSpec("CHILLER01_COND_OUT_TEMP", "Chiller-1 Kondenser Çıkış Sıcaklığı",   23, "°C", 0.1),
    TagSpec("CHILLER01_OIL_TEMP",      "Chiller-1 Yağ Sıcaklığı",               24, "°C", 0.1),
    TagSpec("CHILLER02_EVAP_IN_TEMP",  "Chiller-2 Evaporatör Giriş Sıcaklığı",  25, "°C", 0.1),
    TagSpec("CHILLER02_EVAP_OUT_TEMP", "Chiller-2 Evaporatör Çıkış Sıcaklığı",  26, "°C", 0.1),
    TagSpec("CHILLER02_COND_IN_TEMP",  "Chiller-2 Kondenser Giriş Sıcaklığı",   27, "°C", 0.1),
    TagSpec("CHILLER02_COND_OUT_TEMP", "Chiller-2 Kondenser Çıkış Sıcaklığı",   28, "°C", 0.1),
    TagSpec("CHILLER02_OIL_TEMP",      "Chiller-2 Yağ Sıcaklığı",               29, "°C", 0.1),
    # FCU 01-08 — her biri 2 sıcaklık (16 tag)
    TagSpec("FCU01_SUPPLY_TEMP",  "FCU-1 Üfleme Sıcaklığı",   30, "°C", 0.1),
    TagSpec("FCU01_RETURN_TEMP",  "FCU-1 Dönüş Sıcaklığı",    31, "°C", 0.1),
    TagSpec("FCU02_SUPPLY_TEMP",  "FCU-2 Üfleme Sıcaklığı",   32, "°C", 0.1),
    TagSpec("FCU02_RETURN_TEMP",  "FCU-2 Dönüş Sıcaklığı",    33, "°C", 0.1),
    TagSpec("FCU03_SUPPLY_TEMP",  "FCU-3 Üfleme Sıcaklığı",   34, "°C", 0.1),
    TagSpec("FCU03_RETURN_TEMP",  "FCU-3 Dönüş Sıcaklığı",    35, "°C", 0.1),
    TagSpec("FCU04_SUPPLY_TEMP",  "FCU-4 Üfleme Sıcaklığı",   36, "°C", 0.1),
    TagSpec("FCU04_RETURN_TEMP",  "FCU-4 Dönüş Sıcaklığı",    37, "°C", 0.1),
    TagSpec("FCU05_SUPPLY_TEMP",  "FCU-5 Üfleme Sıcaklığı",   38, "°C", 0.1),
    TagSpec("FCU05_RETURN_TEMP",  "FCU-5 Dönüş Sıcaklığı",    39, "°C", 0.1),
    TagSpec("FCU06_SUPPLY_TEMP",  "FCU-6 Üfleme Sıcaklığı",   40, "°C", 0.1),
    TagSpec("FCU06_RETURN_TEMP",  "FCU-6 Dönüş Sıcaklığı",    41, "°C", 0.1),
    TagSpec("FCU07_SUPPLY_TEMP",  "FCU-7 Üfleme Sıcaklığı",   42, "°C", 0.1),
    TagSpec("FCU07_RETURN_TEMP",  "FCU-7 Dönüş Sıcaklığı",    43, "°C", 0.1),
    TagSpec("FCU08_SUPPLY_TEMP",  "FCU-8 Üfleme Sıcaklığı",   44, "°C", 0.1),
    TagSpec("FCU08_RETURN_TEMP",  "FCU-8 Dönüş Sıcaklığı",    45, "°C", 0.1),
    # Mahal sıcaklıkları (4 tag)
    TagSpec("ZONE01_TEMP", "Bölge-1 Mahal Sıcaklığı (Çarşı)",     46, "°C", 0.1),
    TagSpec("ZONE02_TEMP", "Bölge-2 Mahal Sıcaklığı (Yemek)",     47, "°C", 0.1),
    TagSpec("ZONE03_TEMP", "Bölge-3 Mahal Sıcaklığı (Sinema)",    48, "°C", 0.1),
    TagSpec("ZONE04_TEMP", "Bölge-4 Mahal Sıcaklığı (Otopark)",   49, "°C", 0.1),
]


# ============================================================================
# BASINÇ GRUBU (50 tag, register 50-99, gain 0.01, bar)
# ============================================================================
PRES_TAGS: list[TagSpec] = [
    # AHU 01-04 her biri 2 basınç (8 tag)
    TagSpec("AHU01_FILTER_DP",  "AHU-1 Filtre Basınç Farkı",  50, "bar", 0.01),
    TagSpec("AHU01_FAN_DP",     "AHU-1 Fan Basınç Farkı",     51, "bar", 0.01),
    TagSpec("AHU02_FILTER_DP",  "AHU-2 Filtre Basınç Farkı",  52, "bar", 0.01),
    TagSpec("AHU02_FAN_DP",     "AHU-2 Fan Basınç Farkı",     53, "bar", 0.01),
    TagSpec("AHU03_FILTER_DP",  "AHU-3 Filtre Basınç Farkı",  54, "bar", 0.01),
    TagSpec("AHU03_FAN_DP",     "AHU-3 Fan Basınç Farkı",     55, "bar", 0.01),
    TagSpec("AHU04_FILTER_DP",  "AHU-4 Filtre Basınç Farkı",  56, "bar", 0.01),
    TagSpec("AHU04_FAN_DP",     "AHU-4 Fan Basınç Farkı",     57, "bar", 0.01),
    # Chiller 01-02 her biri 3 basınç (6 tag)
    TagSpec("CHILLER01_EVAP_PRES", "Chiller-1 Evaporatör Basıncı", 58, "bar", 0.01),
    TagSpec("CHILLER01_COND_PRES", "Chiller-1 Kondenser Basıncı",  59, "bar", 0.01),
    TagSpec("CHILLER01_OIL_PRES",  "Chiller-1 Yağ Basıncı",        60, "bar", 0.01),
    TagSpec("CHILLER02_EVAP_PRES", "Chiller-2 Evaporatör Basıncı", 61, "bar", 0.01),
    TagSpec("CHILLER02_COND_PRES", "Chiller-2 Kondenser Basıncı",  62, "bar", 0.01),
    TagSpec("CHILLER02_OIL_PRES",  "Chiller-2 Yağ Basıncı",        63, "bar", 0.01),
    # Sirkülasyon pompaları 01-12 her biri 2 basınç (24 tag)
    *[
        ts
        for i in range(1, 13)
        for ts in (
            TagSpec(f"PUMP{i:02d}_SUCTION_PRES", f"Pompa-{i} Emme Basıncı",
                    64 + (i - 1) * 2, "bar", 0.01),
            TagSpec(f"PUMP{i:02d}_DISCHARGE_PRES", f"Pompa-{i} Basma Basıncı",
                    65 + (i - 1) * 2, "bar", 0.01),
        )
    ],
    # Header (kollektör) 3 basınç
    TagSpec("HEADER_HOT_PRES",    "Sıcak Su Kollektör Basıncı",  88, "bar", 0.01),
    TagSpec("HEADER_COLD_PRES",   "Soğuk Su Kollektör Basıncı",  89, "bar", 0.01),
    TagSpec("HEADER_RETURN_PRES", "Dönüş Kollektör Basıncı",     90, "bar", 0.01),
    # Booster 01-02 (2 basınç)
    TagSpec("BOOSTER01_OUT_PRES", "Hidrofor-1 Çıkış Basıncı", 91, "bar", 0.01),
    TagSpec("BOOSTER02_OUT_PRES", "Hidrofor-2 Çıkış Basıncı", 92, "bar", 0.01),
    # Press switch / safety (2)
    TagSpec("PRESS_SWITCH_LOW",  "Düşük Basınç Şalteri",  93, "bar", 0.01),
    TagSpec("PRESS_SWITCH_HIGH", "Yüksek Basınç Şalteri", 94, "bar", 0.01),
    # Misc (5 basınç)
    TagSpec("EXP_TANK_PRES",     "Genleşme Tankı Basıncı",  95, "bar", 0.01),
    TagSpec("AIR_SEPARATOR_PRES","Hava Ayırıcı Basıncı",    96, "bar", 0.01),
    TagSpec("GLYCOL_LOOP_PRES",  "Glikol Devresi Basıncı",  97, "bar", 0.01),
    TagSpec("CONDENSATE_PRES",   "Kondens Hattı Basıncı",   98, "bar", 0.01),
    TagSpec("COMPRESSED_AIR_PRES","Basınçlı Hava Basıncı",  99, "bar", 0.01),
]


# ============================================================================
# ENERJİ GRUBU (50 tag, register 100-149, gain 1.0, kWh)
# ============================================================================
ENERGY_TAGS: list[TagSpec] = [
    # Ana pano (3)
    TagSpec("ENERGY_MAIN_KWH",  "Ana Pano Enerji Sayacı",          100, "kWh", 1.0),
    TagSpec("ENERGY_MAIN_KVAR", "Ana Pano Reaktif Enerji",         101, "kVar", 1.0),
    TagSpec("ENERGY_MAIN_PF",   "Ana Pano Güç Faktörü",            102, "x100", 1.0),
    # Alt pano (4)
    TagSpec("ENERGY_HVAC_KWH",     "HVAC Pano Enerji",       103, "kWh", 1.0),
    TagSpec("ENERGY_LIGHTING_KWH", "Aydınlatma Pano Enerji", 104, "kWh", 1.0),
    TagSpec("ENERGY_ELEVATOR_KWH", "Asansör Pano Enerji",    105, "kWh", 1.0),
    TagSpec("ENERGY_KITCHEN_KWH",  "Mutfak Pano Enerji",     106, "kWh", 1.0),
    # Chiller 01-02 (2)
    TagSpec("CHILLER01_KWH", "Chiller-1 Enerji Tüketimi", 107, "kWh", 1.0),
    TagSpec("CHILLER02_KWH", "Chiller-2 Enerji Tüketimi", 108, "kWh", 1.0),
    # AHU 01-04 (4)
    TagSpec("AHU01_KWH", "AHU-1 Enerji",  109, "kWh", 1.0),
    TagSpec("AHU02_KWH", "AHU-2 Enerji",  110, "kWh", 1.0),
    TagSpec("AHU03_KWH", "AHU-3 Enerji",  111, "kWh", 1.0),
    TagSpec("AHU04_KWH", "AHU-4 Enerji",  112, "kWh", 1.0),
    # Pompa 01-12 (12)
    *[
        TagSpec(f"PUMP{i:02d}_KWH", f"Pompa-{i} Enerji", 113 + i - 1, "kWh", 1.0)
        for i in range(1, 13)
    ],
    # FCU 01-08 (8)
    *[
        TagSpec(f"FCU{i:02d}_KWH", f"FCU-{i} Enerji", 125 + i - 1, "kWh", 1.0)
        for i in range(1, 9)
    ],
    # Booster 01-02 (2)
    TagSpec("BOOSTER01_KWH", "Hidrofor-1 Enerji", 133, "kWh", 1.0),
    TagSpec("BOOSTER02_KWH", "Hidrofor-2 Enerji", 134, "kWh", 1.0),
    # Cooling tower 01-02 (2)
    TagSpec("COOLING_TOWER01_KWH", "Soğutma Kulesi-1 Enerji", 135, "kWh", 1.0),
    TagSpec("COOLING_TOWER02_KWH", "Soğutma Kulesi-2 Enerji", 136, "kWh", 1.0),
    # Asansör 01-02 (2)
    TagSpec("ELEVATOR01_KWH", "Asansör-1 Enerji", 137, "kWh", 1.0),
    TagSpec("ELEVATOR02_KWH", "Asansör-2 Enerji", 138, "kWh", 1.0),
    # Otopark + acil (2)
    TagSpec("PARKING_VENT_KWH",  "Otopark Havalandırma Enerji", 139, "kWh", 1.0),
    TagSpec("EMERGENCY_GEN_KWH", "Acil Jeneratör Enerji",       140, "kWh", 1.0),
    # UPS + güvenlik (2)
    TagSpec("UPS_KWH",      "UPS Enerji Tüketimi",  141, "kWh", 1.0),
    TagSpec("SECURITY_KWH", "Güvenlik Pano Enerji", 142, "kWh", 1.0),
    # Solar + grid (3)
    TagSpec("SOLAR_GEN_KWH",   "Güneş Enerjisi Üretimi",  143, "kWh", 1.0),
    TagSpec("GRID_IMPORT_KWH", "Şebeke Çekiş",            144, "kWh", 1.0),
    TagSpec("GRID_EXPORT_KWH", "Şebeke Veriş",            145, "kWh", 1.0),
    # Boiler + sıcak su (2)
    TagSpec("WATER_HEATING_KWH", "Sıcak Su Hazırlama Enerji", 146, "kWh", 1.0),
    TagSpec("BOILER_KWH",        "Kazan Enerji",              147, "kWh", 1.0),
    # Mahal grup (2)
    TagSpec("OFFICE_KWH",      "Ofis Bölgesi Enerji",     148, "kWh", 1.0),
    TagSpec("COMMON_AREA_KWH", "Ortak Alan Enerji",       149, "kWh", 1.0),
]


# ============================================================================
# RPM GRUBU (30 tag, register 150-179, gain 1.0)
# ============================================================================
RPM_TAGS: list[TagSpec] = [
    # AHU fan (4)
    *[
        TagSpec(f"AHU{i:02d}_FAN_RPM", f"AHU-{i} Fan Devri", 149 + i, "rpm", 1.0)
        for i in range(1, 5)
    ],
    # Sirkülasyon pompası (12)
    *[
        TagSpec(f"PUMP{i:02d}_RPM", f"Pompa-{i} Devri", 153 + i, "rpm", 1.0)
        for i in range(1, 13)
    ],
    # FCU fan (8)
    *[
        TagSpec(f"FCU{i:02d}_FAN_RPM", f"FCU-{i} Fan Devri", 165 + i, "rpm", 1.0)
        for i in range(1, 9)
    ],
    # Cooling tower fan (2)
    TagSpec("COOLING_TOWER01_FAN_RPM", "Soğutma Kulesi-1 Fan Devri", 174, "rpm", 1.0),
    TagSpec("COOLING_TOWER02_FAN_RPM", "Soğutma Kulesi-2 Fan Devri", 175, "rpm", 1.0),
    # Chiller compressor (2)
    TagSpec("CHILLER01_COMP_RPM", "Chiller-1 Kompresör Devri", 176, "rpm", 1.0),
    TagSpec("CHILLER02_COMP_RPM", "Chiller-2 Kompresör Devri", 177, "rpm", 1.0),
    # Booster (2)
    TagSpec("BOOSTER01_RPM", "Hidrofor-1 Devri", 178, "rpm", 1.0),
    TagSpec("BOOSTER02_RPM", "Hidrofor-2 Devri", 179, "rpm", 1.0),
]


# ============================================================================
# STATUS GRUBU (20 tag, register 180-199, gain 1.0, 0/1)
# ============================================================================
STATUS_TAGS: list[TagSpec] = [
    # Chiller running + alarm (4)
    TagSpec("CHILLER01_RUNNING", "Chiller-1 Çalışma Durumu",  180, "", 1.0),
    TagSpec("CHILLER02_RUNNING", "Chiller-2 Çalışma Durumu",  181, "", 1.0),
    TagSpec("CHILLER01_ALARM",   "Chiller-1 Alarm Durumu",    182, "", 1.0),
    TagSpec("CHILLER02_ALARM",   "Chiller-2 Alarm Durumu",    183, "", 1.0),
    # AHU running (4)
    TagSpec("AHU01_RUNNING", "AHU-1 Çalışma Durumu", 184, "", 1.0),
    TagSpec("AHU02_RUNNING", "AHU-2 Çalışma Durumu", 185, "", 1.0),
    TagSpec("AHU03_RUNNING", "AHU-3 Çalışma Durumu", 186, "", 1.0),
    TagSpec("AHU04_RUNNING", "AHU-4 Çalışma Durumu", 187, "", 1.0),
    # Booster running (2)
    TagSpec("BOOSTER01_RUNNING", "Hidrofor-1 Çalışma Durumu", 188, "", 1.0),
    TagSpec("BOOSTER02_RUNNING", "Hidrofor-2 Çalışma Durumu", 189, "", 1.0),
    # Yangın + acil (2)
    TagSpec("FIRE_ALARM",     "Yangın Alarmı",       190, "", 1.0),
    TagSpec("EMERGENCY_STOP", "Acil Stop Aktif",     191, "", 1.0),
    # Güç + UPS (2)
    TagSpec("POWER_OK",    "Şebeke Sağlam",  192, "", 1.0),
    TagSpec("UPS_RUNNING", "UPS Devrede",    193, "", 1.0),
    # Asansör running (2)
    TagSpec("ELEVATOR01_RUNNING", "Asansör-1 Çalışma Durumu", 194, "", 1.0),
    TagSpec("ELEVATOR02_RUNNING", "Asansör-2 Çalışma Durumu", 195, "", 1.0),
    # Lift station (2)
    TagSpec("LIFT_PUMP01_RUNNING", "Foseptik Pompası-1 Çalışma", 196, "", 1.0),
    TagSpec("LIFT_PUMP02_RUNNING", "Foseptik Pompası-2 Çalışma", 197, "", 1.0),
    # Güvenlik (2)
    TagSpec("SECURITY_ARMED",     "Güvenlik Sistemi Aktif", 198, "", 1.0),
    TagSpec("GAS_LEAK_DETECTED",  "Gaz Kaçağı Tespiti",     199, "", 1.0),
]


# CSV header (bulk_import.BulkImportRow ile bire bir eşleşir)
_CSV_COLUMNS: tuple[str, ...] = (
    "tag_id",
    "name",
    "modbus_host",
    "modbus_port",
    "unit_id",
    "register_address",
    "register_type",
    "byte_order",
    "gain",
    "offset",
    "unit",
    "polling_interval_ms",
)


def _polling_interval_ms(tag_index: int) -> int:
    """1-tabanlı tag indeksine göre polling preset dağılımı.

    1..150  → 10000 ms (slow)   — temp + pres + energy
    151..195 → 1000 ms (normal) — RPM + ilk 15 status
    196..200 → 100 ms (fast)    — son 5 status
    """
    if tag_index <= _POLLING_SLOW_MAX:
        return 10000
    if tag_index <= _POLLING_NORMAL_MAX:
        return 1000
    return 100


def all_tags() -> list[TagSpec]:
    """Tüm 200 tag'i sıralı (register sırasında) döndürür."""
    tags = TEMP_TAGS + PRES_TAGS + ENERGY_TAGS + RPM_TAGS + STATUS_TAGS
    if len(tags) != 200:
        raise ValueError(f"Toplam tag sayısı 200 olmalı, mevcut: {len(tags)}")
    # Register doğrulaması: 0..199 hepsi tek seferde
    registers = sorted(t.register for t in tags)
    expected = list(range(200))
    if registers != expected:
        diff = set(expected) ^ set(registers)
        raise ValueError(
            f"Register adresleri 0..199 arasında benzersiz olmalı; eksik/dup: {diff}"
        )
    return tags


def generate_csv(
    output_path: Path,
    modbus_host: str = "127.0.0.1",
    modbus_port: int = 502,
    unit_id: int = 1,
) -> int:
    """Tag CSV'sini diske yazar, üretilen tag adedini döndürür."""
    tags = all_tags()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_COLUMNS)
        writer.writeheader()
        for index, t in enumerate(tags, start=1):
            writer.writerow({
                "tag_id": t.tag_id,
                "name": t.name,
                "modbus_host": modbus_host,
                "modbus_port": modbus_port,
                "unit_id": unit_id,
                "register_address": _MODBUS_HOLDING_BASE + t.register,
                "register_type": "uint16",
                "byte_order": "big",
                "gain": t.gain,
                "offset": 0.0,
                "unit": t.unit,
                "polling_interval_ms": _polling_interval_ms(index),
            })
    return len(tags)


def main() -> int:
    """CLI giriş noktası."""
    parser = argparse.ArgumentParser(
        description="Endurance test için 200 tag'lik AVM HVAC anlamlı CSV üretir"
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("_personal/endurance/endurance_avm_tags_200.csv"),
        help="Çıktı dosyası yolu (varsayılan: _personal/endurance/endurance_avm_tags_200.csv)",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Tag'lerin Modbus host adresi (varsayılan: 127.0.0.1 — diagslave loopback)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=502,
        help="Modbus TCP portu (varsayılan: 502 — diagslave standart)",
    )
    parser.add_argument(
        "--unit-id",
        type=int,
        default=1,
        help="Modbus unit_id (varsayılan: 1)",
    )
    args = parser.parse_args()
    count = generate_csv(args.out, args.host, args.port, args.unit_id)
    print(f"OK: {count} tag yazıldı → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
