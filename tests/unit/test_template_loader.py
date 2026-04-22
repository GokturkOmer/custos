"""F9 AVM Template Pack YAML loader birim testleri.

pyyaml + pydantic yüklü ortamda DB bağlantısı gerektirmez. `tmp_path`
fixture'ı ile küçük YAML dosyaları yazıp loader davranışını doğrular.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from custos.analytics.templates.loader import (
    TemplateLoadError,
    TemplateSchema,
    default_template_dir,
    load_template_file,
    load_templates,
)

_MINIMAL_YAML = """\
slug: sample_asset
name: Sample Asset
description: Unit test için küçük şablon
icon: activity
roles:
  - role_key: inlet_temp
    label: Giriş Sıcaklığı
    unit_hint: °C
    required: true
    sort_order: 1
  - role_key: outlet_temp
    label: Çıkış Sıcaklığı
    unit_hint: °C
    required: true
    sort_order: 2
kpis:
  - name: delta_t
    formula: outlet_temp - inlet_temp
    unit: °C
    description: Sıcaklık farkı
alarm_defaults:
  - role_key: outlet_temp
    direction: high
    set_point: 80.0
    severity: warn
    debounce_seconds: 30
maintenance_defaults:
  - title: Aylık kontrol
    period_days: 30
    description: Aylık görsel kontrol
"""


def _write(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def test_load_template_file_minimal(tmp_path: Path) -> None:
    """Geçerli YAML başarıyla parse edilir, alt alanlar tip korur."""
    path = _write(tmp_path, "sample.yaml", _MINIMAL_YAML)
    schema = load_template_file(path)

    assert isinstance(schema, TemplateSchema)
    assert schema.slug == "sample_asset"
    assert len(schema.roles) == 2
    assert schema.roles[0].role_key == "inlet_temp"
    assert len(schema.kpis) == 1
    assert schema.kpis[0].formula == "outlet_temp - inlet_temp"
    assert len(schema.alarm_defaults) == 1
    assert schema.alarm_defaults[0].set_point == pytest.approx(80.0)
    assert len(schema.maintenance_defaults) == 1


def test_load_template_file_missing(tmp_path: Path) -> None:
    """Olmayan dosya TemplateLoadError fırlatır."""
    with pytest.raises(TemplateLoadError, match="bulunamadı"):
        load_template_file(tmp_path / "yok.yaml")


def test_reject_invalid_yaml(tmp_path: Path) -> None:
    """Kötü YAML söz dizimi TemplateLoadError fırlatır."""
    path = _write(tmp_path, "bad.yaml", "slug: [unclosed\n")
    with pytest.raises(TemplateLoadError, match="YAML ayrıştırma"):
        load_template_file(path)


def test_reject_non_dict_root(tmp_path: Path) -> None:
    """YAML kökü liste ise reddedilir."""
    path = _write(tmp_path, "list.yaml", "- item1\n- item2\n")
    with pytest.raises(TemplateLoadError, match="kök öğesi dict"):
        load_template_file(path)


def test_reject_extra_fields(tmp_path: Path) -> None:
    """Şemada olmayan alan reddedilir (extra=forbid)."""
    body = _MINIMAL_YAML + "unknown_field: 123\n"
    path = _write(tmp_path, "extra.yaml", body)
    with pytest.raises(TemplateLoadError, match="doğrulaması başarısız"):
        load_template_file(path)


def test_reject_bad_slug(tmp_path: Path) -> None:
    """Slug snake_case değilse reddedilir."""
    body = _MINIMAL_YAML.replace("slug: sample_asset", "slug: Bad-Slug")
    path = _write(tmp_path, "bad_slug.yaml", body)
    with pytest.raises(TemplateLoadError, match="snake_case"):
        load_template_file(path)


def test_reject_duplicate_role_key(tmp_path: Path) -> None:
    """Aynı role_key iki kez verilirse şema reddeder."""
    body = _MINIMAL_YAML.replace("outlet_temp", "inlet_temp")
    path = _write(tmp_path, "dup_role.yaml", body)
    with pytest.raises(TemplateLoadError, match="role_key tekrarlanıyor"):
        load_template_file(path)


def test_reject_formula_with_unknown_variable(tmp_path: Path) -> None:
    """Formül tanımsız role_key kullanırsa reddedilir."""
    body = _MINIMAL_YAML.replace(
        "formula: outlet_temp - inlet_temp",
        "formula: outlet_temp - nonexistent",
    )
    path = _write(tmp_path, "bad_formula.yaml", body)
    with pytest.raises(TemplateLoadError, match="tanımsız role_key"):
        load_template_file(path)


def test_reject_formula_with_function_call(tmp_path: Path) -> None:
    """Formülde fonksiyon çağrısı (örn. abs) reddedilir — AST guard."""
    body = _MINIMAL_YAML.replace(
        "formula: outlet_temp - inlet_temp",
        "formula: abs(outlet_temp - inlet_temp)",
    )
    path = _write(tmp_path, "call.yaml", body)
    with pytest.raises(TemplateLoadError, match="izinsiz AST node"):
        load_template_file(path)


def test_reject_alarm_pointing_to_unknown_role(tmp_path: Path) -> None:
    """Alarm defaults tanımsız role_key'e bağlanırsa reddedilir."""
    # Yalnızca alarm bölümündeki role_key'i değiştir — roles[] listesinde hâlâ
    # outlet_temp tanımlı kalsın ki KPI formülü değil, cross-reference patlasın.
    body = _MINIMAL_YAML.replace(
        "  - role_key: outlet_temp\n    direction: high",
        "  - role_key: ghost_role\n    direction: high",
    )
    path = _write(tmp_path, "ghost_alarm.yaml", body)
    with pytest.raises(TemplateLoadError, match="alarm_defaults role_key tanımsız"):
        load_template_file(path)


def test_load_templates_directory_alphabetical(tmp_path: Path) -> None:
    """Dizindeki YAML dosyaları alfabetik sırayla yüklenir."""
    _write(tmp_path, "b.yaml", _MINIMAL_YAML.replace("sample_asset", "bravo"))
    _write(tmp_path, "a.yaml", _MINIMAL_YAML.replace("sample_asset", "alpha"))

    loaded = load_templates(tmp_path)
    assert [e.schema.slug for e in loaded] == ["alpha", "bravo"]


def test_load_templates_rejects_duplicate_slug(tmp_path: Path) -> None:
    """Aynı slug iki dosyada varsa yükleme patlar."""
    _write(tmp_path, "x.yaml", _MINIMAL_YAML)
    _write(tmp_path, "y.yaml", _MINIMAL_YAML)

    with pytest.raises(TemplateLoadError, match="Slug tekrarı"):
        load_templates(tmp_path)


def test_load_templates_missing_directory(tmp_path: Path) -> None:
    """Olmayan dizin TemplateLoadError fırlatır."""
    with pytest.raises(TemplateLoadError, match="dizini bulunamadı"):
        load_templates(tmp_path / "yok")


def test_default_template_dir_is_project_root_templates() -> None:
    """default_template_dir proje kökü altındaki templates/ klasörünü gösterir."""
    path = default_template_dir()
    assert path.name == "templates"
    assert path.is_absolute()


def test_to_asset_template_populates_children() -> None:
    """TemplateSchema → AssetTemplate dataclass dönüşümü roller/KPI dolu döner."""
    schema = TemplateSchema.model_validate(
        {
            "slug": "widget",
            "name": "Widget",
            "roles": [
                {"role_key": "speed", "label": "Hız", "unit_hint": "rpm", "sort_order": 1},
            ],
            "kpis": [
                {"name": "double_speed", "formula": "speed * 2", "unit": "rpm"},
            ],
        },
    )
    tmpl = schema.to_asset_template()
    assert tmpl.slug == "widget"
    assert tmpl.name == "Widget"
    assert [r.role_key for r in tmpl.roles] == ["speed"]
    assert [k.name for k in tmpl.kpi_definitions] == ["double_speed"]
    # template_id upsert sırasında DB tarafından doldurulacak
    assert tmpl.roles[0].template_id == 0
    assert tmpl.kpi_definitions[0].template_id == 0
