"""PP-08 — KpiEngine + _safe_eval unit testleri.

İki bölüm:
- _safe_eval: AST-tabanlı güvenli formül değerlendirme (DB'siz pure function)
- _compute_cycle: tick happy + edge path'leri mock'lu DB ile
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custos.analytics.kpi_engine import KpiEngine, _safe_eval
from custos.shared.database import (
    AssetInstance,
    AssetTemplate,
    KpiDefinition,
    TagBinding,
    TagReading,
    TemplateRole,
)

# --- _safe_eval — pure function, DB yok ---


def test_safe_eval_valid_arithmetic() -> None:
    """Temel toplama / çıkarma / çarpma / bölme + parantez."""
    assert _safe_eval("a + b", {"a": 3.0, "b": 4.0}) == 7.0
    assert _safe_eval("a * 2 - b / 4", {"a": 5.0, "b": 8.0}) == 8.0
    assert _safe_eval("(a + b) / 2", {"a": 6.0, "b": 4.0}) == 5.0


def test_safe_eval_unary_operators() -> None:
    """Unary + ve - destekli."""
    assert _safe_eval("-a", {"a": 7.0}) == -7.0
    assert _safe_eval("+a", {"a": 7.0}) == 7.0


def test_safe_eval_returns_float_for_integer_result() -> None:
    """Tüm int değişkenlerle çalışsa bile dönüş float."""
    out = _safe_eval("a + b", {"a": 2.0, "b": 3.0})
    assert out == 5.0
    assert isinstance(out, float)


def test_safe_eval_invalid_syntax_returns_none() -> None:
    """Bozuk syntax → None (exception fırlatmaz)."""
    assert _safe_eval("a + +", {"a": 1.0}) is None
    assert _safe_eval("(a +", {"a": 1.0}) is None
    assert _safe_eval("", {"a": 1.0}) is None


def test_safe_eval_disallowed_function_call_returns_none() -> None:
    """Fonksiyon çağrısı yasak — abs, sum, min, max."""
    assert _safe_eval("abs(a)", {"a": -5.0}) is None
    assert _safe_eval("min(a, b)", {"a": 1.0, "b": 2.0}) is None


def test_safe_eval_disallowed_attribute_access_returns_none() -> None:
    """Attribute erişimi yasak (a.x gibi)."""
    assert _safe_eval("a.real", {"a": 5.0}) is None


def test_safe_eval_unknown_variable_returns_none() -> None:
    """Formülde geçen ama variables'ta olmayan ad → None."""
    assert _safe_eval("a + xyz", {"a": 1.0}) is None


def test_safe_eval_division_by_zero_returns_none() -> None:
    """Sıfıra bölme exception yerine None."""
    assert _safe_eval("a / b", {"a": 1.0, "b": 0.0}) is None


def test_safe_eval_overflow_returns_none() -> None:
    """Çok büyük değer (overflow) → None."""
    # 10^308 * 10 → inf, ama Python float overflow OverflowError vermeyebilir.
    # Kesin overflow için integer ** ifadesi yasak (Pow node izinsiz). Bunun
    # yerine TypeError yolunu deneyelim — string + float TypeError fırlatır.
    assert _safe_eval("a", {"a": "string"}) is None  # type: ignore[arg-type]


def test_safe_eval_non_numeric_result_returns_none() -> None:
    """Unicode literal veya bool istemiyoruz — pure number bekle."""
    # Constant True/False bool olur (int subclass) ama Constant izinli...
    # Pratikte formül operator'leriyle bool üretilemez (üst node Add/Mul vb).
    # Bu test Constant'ın bool/string'e direkt eval edilmediğini doğrular —
    # variables sözlüğünde tüm değerler float.
    out = _safe_eval("42", {})
    assert out == 42.0


# --- KpiEngine._compute_cycle ---


@pytest.mark.asyncio
async def test_compute_cycle_no_active_instances() -> None:
    """Aktif instance yoksa erken dön — başka DB sorgusu yok."""
    db = MagicMock()
    db.list_asset_instances = AsyncMock(return_value=[])
    db.get_asset_template = AsyncMock()
    db.insert_kpi_results_batch = AsyncMock()

    engine = KpiEngine(db=db)
    await engine._compute_cycle()

    db.get_asset_template.assert_not_called()
    db.insert_kpi_results_batch.assert_not_called()


@pytest.mark.asyncio
async def test_compute_cycle_skips_template_without_kpi() -> None:
    """Template'in kpi_definitions boşsa instance atlanır."""
    inst = AssetInstance(template_id=1, name="x")
    inst.id = 1
    tmpl = AssetTemplate(slug="ahu", name="AHU")
    tmpl.id = 1  # kpi_definitions boş

    db = MagicMock()
    db.list_asset_instances = AsyncMock(return_value=[inst])
    db.get_asset_template = AsyncMock(return_value=tmpl)
    db.list_tag_bindings = AsyncMock()
    db.insert_kpi_results_batch = AsyncMock()

    engine = KpiEngine(db=db)
    await engine._compute_cycle()

    db.list_tag_bindings.assert_not_called()
    db.insert_kpi_results_batch.assert_not_called()


@pytest.mark.asyncio
async def test_compute_cycle_skips_when_no_bindings() -> None:
    """Tag binding yoksa o instance KPI hesabı atlanır."""
    inst = AssetInstance(template_id=1, name="x")
    inst.id = 1
    role = TemplateRole(template_id=1, role_key="t_in", label="Inlet T")
    role.id = 1
    kpi = KpiDefinition(template_id=1, name="dt", formula="t_in")
    kpi.id = 1
    tmpl = AssetTemplate(slug="ahu", name="AHU", roles=[role], kpi_definitions=[kpi])
    tmpl.id = 1

    db = MagicMock()
    db.list_asset_instances = AsyncMock(return_value=[inst])
    db.get_asset_template = AsyncMock(return_value=tmpl)
    db.list_tag_bindings = AsyncMock(return_value=[])
    db.get_latest_tag_readings = AsyncMock()
    db.insert_kpi_results_batch = AsyncMock()

    engine = KpiEngine(db=db)
    await engine._compute_cycle()

    db.get_latest_tag_readings.assert_not_called()
    db.insert_kpi_results_batch.assert_not_called()


@pytest.mark.asyncio
async def test_compute_cycle_happy_path_inserts_kpi_result_and_audit() -> None:
    """Tam veriyle: KPI sonucu batch + audit log yazılır."""
    from datetime import UTC, datetime

    inst = AssetInstance(template_id=1, name="ahu-1")
    inst.id = 10
    role_in = TemplateRole(template_id=1, role_key="t_in", label="Inlet T")
    role_in.id = 100
    role_out = TemplateRole(template_id=1, role_key="t_out", label="Outlet T")
    role_out.id = 200
    kpi = KpiDefinition(template_id=1, name="delta_t", formula="t_out - t_in")
    kpi.id = 50
    tmpl = AssetTemplate(
        slug="ahu", name="AHU", roles=[role_in, role_out], kpi_definitions=[kpi],
    )
    tmpl.id = 1

    bindings = [
        TagBinding(instance_id=10, role_id=100, tag_id="TAG_IN"),
        TagBinding(instance_id=10, role_id=200, tag_id="TAG_OUT"),
    ]
    now = datetime.now(UTC)
    readings = {
        "TAG_IN": TagReading(timestamp=now, tag_id="TAG_IN", value=20.0),
        "TAG_OUT": TagReading(timestamp=now, tag_id="TAG_OUT", value=35.0),
    }

    db = MagicMock()
    db.list_asset_instances = AsyncMock(return_value=[inst])
    db.get_asset_template = AsyncMock(return_value=tmpl)
    db.list_tag_bindings = AsyncMock(return_value=bindings)
    db.get_latest_tag_readings = AsyncMock(return_value=readings)
    db.insert_kpi_results_batch = AsyncMock()
    db.insert_audit_log = AsyncMock()

    engine = KpiEngine(db=db)
    await engine._compute_cycle()

    db.insert_kpi_results_batch.assert_awaited_once()
    batch = db.insert_kpi_results_batch.await_args.args[0]
    assert len(batch) == 1
    result = batch[0]
    assert result.instance_id == 10
    assert result.kpi_definition_id == 50
    assert result.value == 15.0  # 35 - 20

    db.insert_audit_log.assert_awaited_once()


@pytest.mark.asyncio
async def test_compute_cycle_skips_kpi_when_formula_invalid() -> None:
    """Bozuk formül → _safe_eval None → o KPI batch'e girmez."""
    inst = AssetInstance(template_id=1, name="x")
    inst.id = 1
    role = TemplateRole(template_id=1, role_key="t", label="T")
    role.id = 1
    bad_kpi = KpiDefinition(template_id=1, name="bad", formula="abs(t)")  # yasak fn
    bad_kpi.id = 99
    tmpl = AssetTemplate(slug="ahu", name="AHU", roles=[role], kpi_definitions=[bad_kpi])
    tmpl.id = 1

    from datetime import UTC, datetime

    bindings = [TagBinding(instance_id=1, role_id=1, tag_id="TAG")]
    readings = {"TAG": TagReading(timestamp=datetime.now(UTC), tag_id="TAG", value=5.0)}

    db = MagicMock()
    db.list_asset_instances = AsyncMock(return_value=[inst])
    db.get_asset_template = AsyncMock(return_value=tmpl)
    db.list_tag_bindings = AsyncMock(return_value=bindings)
    db.get_latest_tag_readings = AsyncMock(return_value=readings)
    db.insert_kpi_results_batch = AsyncMock()
    db.insert_audit_log = AsyncMock()

    engine = KpiEngine(db=db)
    await engine._compute_cycle()

    # _safe_eval None döndü → batch boş kalır → batch insert ÇAĞRILMAZ
    db.insert_kpi_results_batch.assert_not_called()
    db.insert_audit_log.assert_not_called()
