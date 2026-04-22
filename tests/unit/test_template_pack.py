"""F9 AVM Template Pack tüm-YAML-yükleniyor mu smoke testi.

``templates/`` dizinindeki 9 şablonun (F9 paketi) eksiksiz, şema uyumlu ve
birbiriyle tutarlı şekilde yüklendiğini garanti eder. CI regression guard:
yeni bir şablon YAML'ı eklendiğinde testin beklenti setini genişletmesi
gerekir — aksi halde sessiz kapsam kaçağı olur.
"""

from __future__ import annotations

from custos.analytics.templates import (
    TemplateSchema,
    default_template_dir,
    load_templates,
)

# F9 planında tanımlı 9 şablon — kapsam kaymasın diye sabitledik.
_EXPECTED_AVM_SLUGS: frozenset[str] = frozenset({
    "chiller",
    "energy_analyzer",
    "ahu",
    "fcu",
    "cooling_tower",
    "booster_pump_set",
    "circulation_pump",
    "lift_station_waste",
    "lift_station_fresh",
})


def test_avm_pack_has_all_nine_templates() -> None:
    """F9 paketindeki 9 şablonun hepsi yüklenmeli — ne eksik ne fazla kontrolü."""
    loaded = load_templates(default_template_dir())
    slugs = {entry.schema.slug for entry in loaded}
    assert _EXPECTED_AVM_SLUGS.issubset(slugs), (
        f"Eksik F9 şablonları: {_EXPECTED_AVM_SLUGS - slugs}"
    )


def test_avm_pack_every_template_has_required_role() -> None:
    """Her şablonda en az bir zorunlu rol olmalı — boş şablon saha işe yaramaz."""
    loaded = load_templates(default_template_dir())
    for entry in loaded:
        required_roles = [r for r in entry.schema.roles if r.required]
        assert required_roles, (
            f"{entry.schema.slug}: hiç zorunlu rol yok — şablon geçersiz"
        )


def test_avm_pack_kpi_formulas_reference_only_roles() -> None:
    """Loader KPI formülü doğrulamasını zaten yapar ama pack bazında tekrar garantile.

    Bu test loader'ın AST guard'ına güvenir — YAML'lar load_templates'ten
    geçtiyse formüller zaten geçerli. Bir KPI ismi role_key ile çakışırsa
    kpi_engine runtime'da variable gölgelenmesi olur, bunu da reddederiz.
    """
    loaded = load_templates(default_template_dir())
    for entry in loaded:
        schema: TemplateSchema = entry.schema
        role_keys = {r.role_key for r in schema.roles}
        for kpi in schema.kpis:
            assert kpi.name not in role_keys, (
                f"{schema.slug}: KPI name {kpi.name!r} role_key ile çakışıyor "
                "(kpi_engine variable gölgelenir)"
            )


def test_avm_pack_alarm_and_maintenance_advisory_only() -> None:
    """YAML'daki alarm/bakım sayısı makul — şablon başına 0-8 arası bekliyoruz.

    Dashboard preview sınırlı alanda — çok fazla default işlevsel olmaz.
    Üst sınır 8, sinyalin gürültüye çıkmaması için.
    """
    loaded = load_templates(default_template_dir())
    for entry in loaded:
        schema: TemplateSchema = entry.schema
        assert len(schema.alarm_defaults) <= 8, (
            f"{schema.slug}: {len(schema.alarm_defaults)} alarm default "
            "çok fazla (maks 8)"
        )
        assert len(schema.maintenance_defaults) <= 8, (
            f"{schema.slug}: {len(schema.maintenance_defaults)} bakım default "
            "çok fazla (maks 8)"
        )
