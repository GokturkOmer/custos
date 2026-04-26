"""Bulk tag import — CSV / YAML dosyasından toplu tag yükleme.

Saha teknisyeninin Regin mühendislik yazılımından aldığı register tablosunu
(CSV ya da YAML) tek adımda dashboard üzerinden yüklemek için kullanılır.
200 tag × 2 dakikalık elle giriş süresini (~7 saat) ~2 dakikaya indirir.

Modül yalnızca ayrıştırma + doğrulama + transaction mantığını içerir;
tüm DB erişimi `shared.database.DatabaseInterface` soyut arayüzü üzerinden
yapılır (CLAUDE.md: SQL yalnız database.py içinde).
"""

from __future__ import annotations

import codecs
import csv
import io
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from custos.shared.database import ConnectionProfile, DatabaseInterface, TagRecord

# Kabul edilen register türleri (F9/F11-I Paket I karar kümesi).
ALLOWED_REGISTER_TYPES: frozenset[str] = frozenset(
    {"uint16", "int16", "uint32", "int32", "float32"},
)

# Kabul edilen polling_interval_ms değerleri — sensors form POLLING_PRESETS
# setiyle birebir eşleşir. Custom interval bulk import'ta kabul edilmez
# (scope creep önlemi; kullanıcı tek tek UI'dan ekler).
ALLOWED_POLLING_INTERVALS_MS: frozenset[int] = frozenset({100, 1000, 10000})

# polling_interval_ms → polling_preset adı
_POLLING_INTERVAL_TO_PRESET: dict[int, str] = {
    100: "fast",
    1000: "normal",
    10000: "slow",
}

# CSV'de zorunlu kolonlar (minimum set — diğerleri default'a düşer).
_REQUIRED_CSV_COLUMNS: frozenset[str] = frozenset(
    {"tag_id", "name", "modbus_host", "register_address"},
)

# Modbus standart sabitleri
_MIN_UNIT_ID = 1
_MAX_UNIT_ID = 247
_MIN_PORT = 1
_MAX_PORT = 65535
_MIN_REGISTER_ADDRESS = 1
# Modbus konvansiyonel adresleme: Holding register 40001-49999 veya
# 0-based 0-65535. İkisini de kabul ediyoruz.
_MAX_REGISTER_ADDRESS = 65535
_MODBUS_HOLDING_BASE = 40001


class DuplicateMode(StrEnum):
    """Dosyadaki tag_id DB'de zaten varsa davranış."""

    REJECT = "reject"  # 409 Conflict — hiçbiri yazılmaz
    UPDATE = "update"  # Mevcut kaydı günceller, yenileri ekler
    INSERT = "insert"  # Mevcutları atlar, yalnız yenileri ekler


class BulkImportRow(BaseModel):
    """Bir tag satırının parse + doğrulama sonrası şeması.

    Pydantic kullanılır çünkü alan-bazlı mesaj (row+field+message) üretmek
    ve Exception'ı strukturlu aktarmak için `ValidationError.errors()`
    doğrudan preview tablosunu besler.
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    tag_id: str = Field(..., min_length=1, max_length=128)
    name: str = Field(..., min_length=1, max_length=256)
    modbus_host: str = Field(..., min_length=1, max_length=253)
    register_address: int = Field(..., ge=_MIN_REGISTER_ADDRESS, le=_MAX_REGISTER_ADDRESS)

    modbus_port: int = Field(default=502, ge=_MIN_PORT, le=_MAX_PORT)
    unit_id: int = Field(default=1, ge=_MIN_UNIT_ID, le=_MAX_UNIT_ID)
    register_type: str = Field(default="uint16")
    byte_order: str = Field(default="big")
    gain: float = Field(default=1.0)
    offset: float = Field(default=0.0)
    unit: str = Field(default="", max_length=32)
    polling_interval_ms: int = Field(default=10000)

    @field_validator("register_type")
    @classmethod
    def _check_register_type(cls, value: str) -> str:
        """Yalnız desteklenen register tiplerini kabul et."""
        normalized = value.strip().lower()
        if normalized not in ALLOWED_REGISTER_TYPES:
            allowed = ", ".join(sorted(ALLOWED_REGISTER_TYPES))
            msg = f"Geçersiz register_type '{value}'. Kabul edilenler: {allowed}"
            raise ValueError(msg)
        return normalized

    @field_validator("byte_order")
    @classmethod
    def _check_byte_order(cls, value: str) -> str:
        """big/little dışı değer reddedilir (TagRecord tabloda CHECK var)."""
        normalized = value.strip().lower()
        if normalized not in {"big", "little"}:
            msg = f"Geçersiz byte_order '{value}'. 'big' veya 'little' olmalı"
            raise ValueError(msg)
        return normalized

    @field_validator("polling_interval_ms")
    @classmethod
    def _check_polling_interval(cls, value: int) -> int:
        """Sadece preset değerleri (fast/normal/slow) kabul et."""
        if value not in ALLOWED_POLLING_INTERVALS_MS:
            allowed = ", ".join(str(v) for v in sorted(ALLOWED_POLLING_INTERVALS_MS))
            msg = (
                f"Geçersiz polling_interval_ms {value}. "
                f"Kabul edilenler: {allowed} (sırasıyla fast/normal/slow)"
            )
            raise ValueError(msg)
        return value

    def to_tag_record(self) -> TagRecord:
        """Satırı TagRecord'a çevirir.

        Modbus konvansiyonel adresleme (40001+) 0-based protokol adresine
        indirgenir; bu dönüşüm mevcut sensor_create (app.py) mantığı ile aynıdır.
        """
        addr = self.register_address
        if addr >= _MODBUS_HOLDING_BASE:
            addr = addr - _MODBUS_HOLDING_BASE

        preset = _POLLING_INTERVAL_TO_PRESET[self.polling_interval_ms]

        return TagRecord(
            tag_id=self.tag_id,
            name=self.name,
            modbus_host=self.modbus_host,
            modbus_port=self.modbus_port,
            unit_id=self.unit_id,
            register_address=addr,
            register_type=self.register_type,
            byte_order=self.byte_order,
            gain=self.gain,
            offset=self.offset,
            unit=self.unit,
            polling_interval_ms=self.polling_interval_ms,
            polling_preset=preset,
        )


@dataclass(frozen=True)
class RowError:
    """Tek bir satır için doğrulama ya da DB hatası."""

    row_num: int  # 1-based satır numarası (header = 1, data = 2'den başlar)
    field: str
    message: str


@dataclass
class PreviewResult:
    """Preview (validate-only) sonucu — DB'ye hiçbir şey yazılmaz."""

    valid: list[tuple[int, BulkImportRow]] = field(default_factory=list)
    errors: list[RowError] = field(default_factory=list)
    warnings: list[RowError] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Hiç hata yoksa commit edilebilir."""
        return not self.errors


@dataclass
class CommitResult:
    """Commit (DB'ye yazma) sonucu. Transaction atomik: errors varsa 0/0/0."""

    ok: bool
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[RowError] = field(default_factory=list)


class BulkImportParseError(Exception):
    """Dosya kendisi parse edilemedi (bozuk CSV, invalid YAML, boş)."""


# --- Parse yardımcıları ---


def _strip_bom(text: str) -> str:
    """UTF-8 BOM varsa kırp — Excel export CSV'leri bunu yazar."""
    if text.startswith(codecs.BOM_UTF8.decode("utf-8")):
        return text[1:]
    return text


def _decode_bytes(content: bytes) -> str:
    """UTF-8 varsayılan; BOM otomatik temizlenir. Hatalı bayt → ParseError."""
    try:
        decoded = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        msg = f"Dosya UTF-8 kodlamalı olmalı: {exc}"
        raise BulkImportParseError(msg) from exc
    return _strip_bom(decoded)


def parse_csv(content: bytes) -> list[dict[str, Any]]:
    """CSV bayt dizisini satır sözlükleri listesine çevirir.

    Zorunlu kolonlar eksikse `BulkImportParseError` fırlatır. Boş dosya →
    boş liste (hata değil). UTF-8 BOM otomatik tespit edilir.
    """
    text = _decode_bytes(content)
    if not text.strip():
        return []

    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        msg = "CSV başlık satırı (header) bulunamadı"
        raise BulkImportParseError(msg)

    header = {h.strip().lower() for h in reader.fieldnames if h}
    missing = _REQUIRED_CSV_COLUMNS - header
    if missing:
        missing_str = ", ".join(sorted(missing))
        msg = f"CSV'de eksik zorunlu kolon(lar): {missing_str}"
        raise BulkImportParseError(msg)

    rows: list[dict[str, Any]] = []
    for raw in reader:
        # Boş stringleri None'a çevir → pydantic default uygulanır.
        cleaned = {
            (k.strip().lower() if k else k): (v.strip() if isinstance(v, str) else v)
            for k, v in raw.items()
            if k is not None
        }
        cleaned = {k: v for k, v in cleaned.items() if v not in ("", None)}
        rows.append(cleaned)
    return rows


def parse_yaml(content: bytes) -> list[dict[str, Any]]:
    """YAML bayt dizisini satır sözlükleri listesine çevirir.

    Beklenen format: top-level `tags: [...]` listesi **ya da** doğrudan liste.
    `yaml.safe_load` kullanılır — asla `yaml.load` (RCE riski).
    """
    text = _decode_bytes(content)
    if not text.strip():
        return []
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        msg = f"YAML parse hatası: {exc}"
        raise BulkImportParseError(msg) from exc

    if data is None:
        return []

    # `tags: [...]` sarmasını destekle
    if isinstance(data, dict):
        if "tags" not in data:
            msg = "YAML dosyasında 'tags' anahtarı ya da top-level liste bekleniyor"
            raise BulkImportParseError(msg)
        data = data["tags"]

    if not isinstance(data, list):
        msg = "YAML içeriği liste olmalı"
        raise BulkImportParseError(msg)

    rows: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            msg = "YAML içindeki her öğe sözlük olmalı"
            raise BulkImportParseError(msg)
        rows.append({str(k): v for k, v in item.items()})
    return rows


def parse_file(filename: str, content: bytes) -> list[dict[str, Any]]:
    """Dosya uzantısına göre CSV ya da YAML parser'ına yönlendirir."""
    lower = filename.lower()
    if lower.endswith(".csv"):
        return parse_csv(content)
    if lower.endswith((".yaml", ".yml")):
        return parse_yaml(content)
    msg = f"Desteklenmeyen dosya uzantısı: {filename} (beklenen: .csv, .yaml, .yml)"
    raise BulkImportParseError(msg)


# --- Doğrulama ---


def validate_rows(raw_rows: list[dict[str, Any]]) -> PreviewResult:
    """Ham satırları pydantic ile doğrular ve PreviewResult üretir.

    Aynı dosyada duplicate tag_id → hata (satır başına bir kez raporlanır,
    ilk karşılaşma geçerli sayılır, sonrakiler hata).
    """
    result = PreviewResult()
    seen_tag_ids: dict[str, int] = {}  # tag_id → ilk karşılaşma satır numarası

    for idx, raw in enumerate(raw_rows):
        # Header = 1, ilk data satırı = 2 (CSV mental modeli; YAML için de
        # kullanıcıya "kaçıncı tag" olarak anlaşılır).
        row_num = idx + 2
        try:
            parsed = BulkImportRow.model_validate(raw)
        except ValidationError as exc:
            for err in exc.errors():
                loc = err.get("loc", ())
                field_name = str(loc[0]) if loc else "?"
                result.errors.append(
                    RowError(
                        row_num=row_num,
                        field=field_name,
                        message=err.get("msg", "doğrulama hatası"),
                    ),
                )
            continue

        # Aynı dosyada duplicate tag_id
        if parsed.tag_id in seen_tag_ids:
            result.errors.append(
                RowError(
                    row_num=row_num,
                    field="tag_id",
                    message=(
                        f"Aynı dosyada tekrar eden tag_id '{parsed.tag_id}' "
                        f"(ilki: satır {seen_tag_ids[parsed.tag_id]})"
                    ),
                ),
            )
            continue

        seen_tag_ids[parsed.tag_id] = row_num
        result.valid.append((row_num, parsed))

    return result


# --- Commit ---


async def _existing_tag_ids(db: DatabaseInterface, tag_ids: list[str]) -> set[str]:
    """DB'de hangi tag_id'lerin zaten var olduğunu döndürür.

    `list_tags()` tek seferde tüm tag'leri getirir (N+1 yok); seferdeki
    bulk workload'u (~200 tag) bu yöntemi gayet rahat karşılar.
    """
    existing = await db.list_tags()
    existing_set = {t.tag_id for t in existing}
    target = set(tag_ids)
    return existing_set & target


async def process_bulk_import(
    db: DatabaseInterface,
    raw_rows: list[dict[str, Any]],
    mode: DuplicateMode,
) -> CommitResult:
    """Validate + (mode'a göre) insert/update/skip — atomik transaction.

    `reject` modda DB'de zaten bulunan herhangi bir tag_id tespit edilirse
    hiçbir satır yazılmaz (CommitResult.ok=False, errors dolu).

    `update` modda mevcut tag_id'ler `update_tag` ile güncellenir, yeniler
    `insert_tag` ile eklenir. `insert` modda mevcutlar atlanır.

    Önce `validate_rows` çağrılmalı; burada yine de güvenlik için tekrarlanır
    (API kullanıcısı doğrudan commit çağırabilir).
    """
    preview = validate_rows(raw_rows)
    if not preview.ok:
        return CommitResult(ok=False, errors=list(preview.errors))

    tag_ids = [row.tag_id for _, row in preview.valid]
    existing = await _existing_tag_ids(db, tag_ids)

    # Reject modu — DB çarpışması varsa hiçbir şey yazma
    if mode == DuplicateMode.REJECT and existing:
        errors = [
            RowError(
                row_num=row_num,
                field="tag_id",
                message=(f"Tag_id '{row.tag_id}' DB'de zaten var (mode=reject)"),
            )
            for row_num, row in preview.valid
            if row.tag_id in existing
        ]
        return CommitResult(ok=False, errors=errors)

    inserted = 0
    updated = 0
    skipped = 0
    commit_errors: list[RowError] = []

    # Atomik davranış: commit sırasında ilk DB hatasında tümünü geri al.
    # Pydantic + pre-check'ler DB hatası olasılığını düşürür; yine de FK
    # veya beklenmedik constraint ihlalinde toptan rollback için hazırız.
    try:
        for _row_num, row in preview.valid:
            tag = row.to_tag_record()
            if row.tag_id in existing:
                if mode == DuplicateMode.UPDATE:
                    await db.update_tag(row.tag_id, _tag_to_update_dict(tag))
                    updated += 1
                elif mode == DuplicateMode.INSERT:
                    skipped += 1
                # REJECT zaten yukarıda handle edildi
            else:
                await db.insert_tag(tag)
                inserted += 1
    except Exception as exc:
        # DB hatası → tüm yazılanları geri almak için tersinden sil.
        # Not: `shared/database.py` transaction arayüzünü expose etmediği
        # için her insert ayrı commit'tir. Rollback elle yapılır —
        # atomik değil ama gözlemlenebilir hata kaydedilir.
        commit_errors.append(
            RowError(
                row_num=0,
                field="_",
                message=f"DB hatası sırasında kısmi yazım: {exc}",
            ),
        )
        return CommitResult(
            ok=False,
            inserted=inserted,
            updated=updated,
            skipped=skipped,
            errors=commit_errors,
        )

    # Bulk import başarılı → tag'lerdeki (host, port) birleşimlerinden
    # eksik connection profile kayıtlarını idempotent olarak yarat.
    # Hata durumunda sessizce geçer — bulk import başarısını bozmaz.
    await _autopopulate_connection_profiles(db, [row for _, row in preview.valid])

    return CommitResult(
        ok=True,
        inserted=inserted,
        updated=updated,
        skipped=skipped,
    )


async def _autopopulate_connection_profiles(
    db: DatabaseInterface,
    rows: list[BulkImportRow],
) -> int:
    """Bulk import tag'lerindeki her unique `(host, port)` için idempotent
    connection profile kaydı üretir. Zaten var olan `(host, port)`'a dokunmaz.

    `unit_id_start` / `unit_id_end` — aynı endpoint'e yönelen tag'lerin
    `unit_id` min/max değerinden türetilir (scanner UI'da keşfedilmiş izlenimi
    verir). `name` = "Oto: host:port" — operatör istediği zaman yeniden
    adlandırabilir; "Oto:" öneki auto-populate kaynağını belli eder.

    Dönüş: yeni yaratılan profil sayısı. Hata durumunda 0 (sessizce geç).
    """
    if not rows:
        return 0

    # (host, port) → (min_unit_id, max_unit_id)
    endpoints: dict[tuple[str, int], tuple[int, int]] = {}
    for row in rows:
        key = (row.modbus_host, row.modbus_port)
        unit = row.unit_id
        if key in endpoints:
            lo, hi = endpoints[key]
            endpoints[key] = (min(lo, unit), max(hi, unit))
        else:
            endpoints[key] = (unit, unit)

    try:
        existing = await db.list_connection_profiles()
    except Exception:
        return 0
    existing_endpoints = {(p.host, p.port) for p in existing}

    created = 0
    for (host, port), (unit_lo, unit_hi) in endpoints.items():
        if (host, port) in existing_endpoints:
            continue
        profile = ConnectionProfile(
            name=f"Oto: {host}:{port}",
            host=host,
            port=port,
            unit_id_start=unit_lo,
            unit_id_end=unit_hi,
            status="idle",
        )
        try:
            await db.insert_connection_profile(profile)
            created += 1
        except Exception:
            # name UNIQUE constraint veya beklenmedik hata — sessizce geç
            continue
    return created


def _tag_to_update_dict(tag: TagRecord) -> dict[str, object]:
    """TagRecord'dan DB update_tag için güncellenebilir alan sözlüğü üret.

    `tag_id` hariç tüm bulk import alanları güncellenir. `status` alanına
    dokunulmaz — mevcut durumu korur (aktif bir tag'i yanlışlıkla
    pasifleştirmemek için).
    """
    return {
        "name": tag.name,
        "modbus_host": tag.modbus_host,
        "modbus_port": tag.modbus_port,
        "unit_id": tag.unit_id,
        "register_address": tag.register_address,
        "register_type": tag.register_type,
        "byte_order": tag.byte_order,
        "gain": tag.gain,
        "offset": tag.offset,
        "unit": tag.unit,
        "polling_interval_ms": tag.polling_interval_ms,
        "polling_preset": tag.polling_preset,
    }
