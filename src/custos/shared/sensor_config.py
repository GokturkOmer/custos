"""Sensör konfigürasyon yükleyici.

.. deprecated::
    Bu modül Aşama 3 (walking skeleton) için yazılmıştır.
    Aşama 4 F2 ile birlikte Collector artık tag tanımlarını
    veritabanındaki ``tags`` tablosundan okumaktadır.
    Yeni kod ``shared.database.TagRecord`` kullanmalıdır.
    Bu dosya geriye dönük uyumluluk için korunmaktadır.

TOML dosyasından sensör tanımlarını okur ve tip güvenli
pydantic modelleri ile doğrular.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class SensorConfig(BaseModel):
    """Tek bir sensörün konfigürasyonu.

    TOML dosyasındaki [[sensors]] bloğunun tip güvenli temsili.
    """

    id: str
    name: str
    modbus_host: str
    modbus_port: int
    unit_id: int
    register_address: int = Field(alias="register")
    register_type: Literal["int16", "uint16", "int32", "uint32", "float32"]
    scale_factor: float = 1.0
    min_value: float
    max_value: float
    unit: str
    read_interval_seconds: int


class _SensorsFile(BaseModel):
    """TOML dosyasının kök yapısı."""

    sensors: list[SensorConfig]


def load_sensor_configs(path: Path) -> list[SensorConfig]:
    """TOML dosyasından sensör konfigürasyonlarını yükler.

    Args:
        path: Sensör konfigürasyon dosyasının yolu.

    Returns:
        Doğrulanmış sensör konfigürasyonlarının listesi.

    Raises:
        FileNotFoundError: Dosya bulunamazsa.
        ValueError: Dosya geçersizse veya doğrulama başarısızsa.
    """
    if not path.exists():
        msg = f"Sensör konfigürasyon dosyası bulunamadı: {path}"
        raise FileNotFoundError(msg)

    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as exc:
        msg = f"Sensör konfigürasyon dosyası geçersiz TOML: {path}"
        raise ValueError(msg) from exc

    try:
        parsed = _SensorsFile.model_validate(data)
    except Exception as exc:
        msg = f"Sensör konfigürasyon doğrulaması başarısız: {exc}"
        raise ValueError(msg) from exc

    return parsed.sensors
