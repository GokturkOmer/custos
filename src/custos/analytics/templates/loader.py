"""AVM asset template YAML loader (F9 Paket A).

Kök dizindeki ``templates/`` klasöründeki ``.yaml`` dosyalarını okur, pydantic
ile doğrular, ``AssetTemplate`` dataclass'ına dönüştürür. KPI formül AST
doğrulaması ``kpi_engine`` ile aynı kurallar üzerinden yapılır: fonksiyon
çağrısı, attribute erişimi, import yasaktır; sadece aritmetik operatörler
(+, -, \\*, /) ve role_key referansı kabul edilir.

Alarm ve bakım varsayılanları YAML'da taşınır; seed aşamasında DB'ye yazılmaz
(sadece template_roles + kpi_definitions seed edilir). Preview ve instance
oluşturma sırasında tekrar okunurlar.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from custos.shared.database import (
    AssetTemplate,
    KpiDefinition,
    TemplateRole,
)

# KPI formül AST doğrulamasında izin verilen node tipleri.
# ``kpi_engine._ALLOWED_NODE_TYPES`` ile aynı kümede tutulur — kpi engine
# runtime'da bu formülü değerlendirecektir, YAML yüklemesinde önden reddedilir.
_ALLOWED_FORMULA_NODE_TYPES: frozenset[type] = frozenset({
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.Constant,
    ast.Name,
    ast.Load,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.USub,
    ast.UAdd,
})

# Slug / role_key / KPI adı için kabul edilen tek biçim — snake_case küçük harf.
_SLUG_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789_"


class TemplateLoadError(ValueError):
    """YAML doğrulama veya dosya okuma hatası.

    Template loader birden fazla dosyayı sırayla yükler; bir dosya patladığında
    mesaj içine hangi dosya olduğunu katar ki operatör doğru YAML'ı düzeltsin.
    """


def _is_snake_case(value: str) -> bool:
    """Küçük harf, rakam, alt çizgi; ilk karakter harf olmalı."""
    if not value or not value[0].isalpha():
        return False
    return all(c in _SLUG_CHARS for c in value)


class RoleSchema(BaseModel):
    """Template rolü — asset binding sırasında doldurulacak tag yuvası."""

    model_config = ConfigDict(extra="forbid")

    role_key: str = Field(..., min_length=1, max_length=64)
    label: str = Field(..., min_length=1, max_length=120)
    unit_hint: str = ""
    required: bool = True
    sort_order: int = 0

    @model_validator(mode="after")
    def _validate_role_key(self) -> RoleSchema:
        if not _is_snake_case(self.role_key):
            raise ValueError(
                f"role_key snake_case olmalı (küçük harf + rakam + _): {self.role_key!r}",
            )
        return self


class KpiSchema(BaseModel):
    """KPI tanımı — AST tabanlı aritmetik formül."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=64)
    formula: str = Field(..., min_length=1)
    unit: str = ""
    description: str = ""

    @model_validator(mode="after")
    def _validate_name(self) -> KpiSchema:
        if not _is_snake_case(self.name):
            raise ValueError(
                f"KPI name snake_case olmalı: {self.name!r}",
            )
        return self


class AlarmDefaultSchema(BaseModel):
    """Advisory alarm eşiği — YAML'dan okunur, DB'ye yazılmaz.

    Instance oluşturulup tag bind edildikten sonra kullanıcı bu önerileri
    threshold_engine'e aktarabilir. F9 kapsamında sadece preview metadata.
    """

    model_config = ConfigDict(extra="forbid")

    role_key: str
    direction: Literal["high", "low"] = "high"
    set_point: float
    severity: Literal["warn", "crit"] = "warn"
    debounce_seconds: int = Field(5, ge=0, le=3600)
    hysteresis: float = Field(0.0, ge=0.0)
    description: str = ""


class MaintenanceDefaultSchema(BaseModel):
    """Advisory bakım planı — YAML'dan okunur, DB'ye yazılmaz.

    F8a maintenance modülü instance bazlı çalışır. F9 kapsamında bu alanlar
    sadece dashboard preview'inde gösterilir, otomatik schedule oluşturulmaz.
    """

    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=1, max_length=120)
    period_days: int = Field(..., ge=1, le=3650)
    description: str = ""
    checklist_slug: str | None = None


class TemplateSchema(BaseModel):
    """F9 AVM Template Pack YAML şeması."""

    model_config = ConfigDict(extra="forbid")

    slug: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=120)
    description: str = ""
    icon: str = "activity"
    roles: list[RoleSchema] = Field(..., min_length=1)
    kpis: list[KpiSchema] = Field(default_factory=list)
    alarm_defaults: list[AlarmDefaultSchema] = Field(default_factory=list)
    maintenance_defaults: list[MaintenanceDefaultSchema] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_cross_references(self) -> TemplateSchema:
        if not _is_snake_case(self.slug):
            raise ValueError(f"slug snake_case olmalı: {self.slug!r}")

        # Duplicate role_key kontrolü
        role_keys: set[str] = set()
        for role in self.roles:
            if role.role_key in role_keys:
                raise ValueError(f"role_key tekrarlanıyor: {role.role_key!r}")
            role_keys.add(role.role_key)

        # Duplicate KPI adı kontrolü
        kpi_names: set[str] = set()
        for kpi in self.kpis:
            if kpi.name in kpi_names:
                raise ValueError(f"KPI name tekrarlanıyor: {kpi.name!r}")
            kpi_names.add(kpi.name)

            # Formül AST doğrulama — kpi_engine ile aynı kurallar
            _validate_formula_ast(kpi.formula, allowed_variables=role_keys)

        # Alarm default'larının role_key'leri gerçekten tanımlı mı?
        for alarm in self.alarm_defaults:
            if alarm.role_key not in role_keys:
                raise ValueError(
                    f"alarm_defaults role_key tanımsız: {alarm.role_key!r}",
                )

        return self

    def to_asset_template(self) -> AssetTemplate:
        """YAML şemasını DB dataclass'ına dönüştürür (roller + KPI dahil).

        ``alarm_defaults`` ve ``maintenance_defaults`` DB'ye yazılmaz; çağıran
        tarafta ayrıca saklanır (loader + dashboard preview).
        """
        tmpl = AssetTemplate(
            slug=self.slug,
            name=self.name,
            description=self.description,
            icon=self.icon,
        )
        tmpl.roles = [
            TemplateRole(
                template_id=0,  # upsert sırasında DB atar
                role_key=r.role_key,
                label=r.label,
                unit_hint=r.unit_hint,
                required=r.required,
                sort_order=r.sort_order,
            )
            for r in self.roles
        ]
        tmpl.kpi_definitions = [
            KpiDefinition(
                template_id=0,
                name=k.name,
                formula=k.formula,
                unit=k.unit,
                description=k.description,
            )
            for k in self.kpis
        ]
        return tmpl


def _validate_formula_ast(formula: str, allowed_variables: set[str]) -> None:
    """KPI formülünün kpi_engine._safe_eval ile uyumlu olduğunu doğrular.

    Ağacı walk eder, yalnızca ``_ALLOWED_FORMULA_NODE_TYPES`` içindeki
    node'lara ve ``allowed_variables`` içindeki ``Name`` referanslarına
    izin verir. Hatalı formül doğrudan ``ValueError`` fırlatır.
    """
    try:
        tree = ast.parse(formula, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"KPI formülü ayrıştırılamadı: {formula!r} ({exc})") from exc

    for node in ast.walk(tree):
        if type(node) not in _ALLOWED_FORMULA_NODE_TYPES:
            raise ValueError(
                f"KPI formülünde izinsiz AST node: {type(node).__name__} "
                f"({formula!r})",
            )
        if isinstance(node, ast.Name) and node.id not in allowed_variables:
            raise ValueError(
                f"KPI formülü tanımsız role_key referansı içeriyor: "
                f"{node.id!r} ({formula!r})",
            )


@dataclass(frozen=True)
class LoadedTemplate:
    """Yüklenmiş bir şablonun YAML dosyası + doğrulanmış şeması."""

    path: Path
    schema: TemplateSchema


def default_template_dir() -> Path:
    """Proje kökündeki ``templates/`` klasörü — YAML dosyalarının yeri."""
    # loader.py konumu: <root>/src/custos/analytics/templates/loader.py
    # Hedef: <root>/templates
    return Path(__file__).resolve().parents[4] / "templates"


def load_template_file(path: Path) -> TemplateSchema:
    """Tek bir YAML dosyasını okuyup şemasını döndürür.

    Dosya yoksa ``TemplateLoadError`` fırlatır; YAML ayrıştırma veya şema
    doğrulama başarısız olursa dosya adını mesaja ekler.
    """
    if not path.is_file():
        raise TemplateLoadError(f"Template dosyası bulunamadı: {path}")

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise TemplateLoadError(f"{path}: okunamadı ({exc})") from exc

    try:
        data: Any = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise TemplateLoadError(f"{path}: YAML ayrıştırma hatası ({exc})") from exc

    if not isinstance(data, dict):
        raise TemplateLoadError(
            f"{path}: YAML kök öğesi dict olmalı (bulunan: {type(data).__name__})",
        )

    try:
        return TemplateSchema(**data)
    except Exception as exc:
        raise TemplateLoadError(f"{path}: şema doğrulaması başarısız ({exc})") from exc


def load_templates(template_dir: Path | None = None) -> list[LoadedTemplate]:
    """Dizindeki tüm ``.yaml`` dosyalarını okuyup sıralı liste döndürür.

    Slug tekrarı yasak — iki YAML aynı slug kullanırsa ``TemplateLoadError``.
    Dosya adlarına göre alfabetik sıra: reproducible seed output.
    """
    target_dir = template_dir if template_dir is not None else default_template_dir()
    if not target_dir.is_dir():
        raise TemplateLoadError(f"Template dizini bulunamadı: {target_dir}")

    yaml_files = sorted(target_dir.glob("*.yaml"))
    loaded: list[LoadedTemplate] = []
    seen_slugs: dict[str, Path] = {}
    for path in yaml_files:
        schema = load_template_file(path)
        if schema.slug in seen_slugs:
            raise TemplateLoadError(
                f"Slug tekrarı: {schema.slug!r} hem {seen_slugs[schema.slug]} "
                f"hem de {path} dosyasında tanımlı",
            )
        seen_slugs[schema.slug] = path
        loaded.append(LoadedTemplate(path=path, schema=schema))

    return loaded
