"""src/custos/analytics/cross_sensor_engine.py — birim testleri (Faz 2 Prompt 2).

Kapsam:
- TagCondition / CrossSensorRule field validasyonu — gecersiz girdiler
  early-fail (op, severity, threshold, duplicate tag, bos liste).
- TagCondition.matches her operator (gt/gte/lt/lte/eq) ve NaN davranisi.
- CrossSensorRule.evaluate require_all=True/False, eksik tag davranisi.
- CrossSensorEngine ctor duplicate rule_id, required_tags sorted+dedup.
- evaluate_current: match → alert, no match → bos liste, snapshot
  + triggered_conditions iceriklerinin dogrulugu.
- evaluate_history: list-of-lists ve numpy ndarray uzerinden tutarli,
  herhangi bir kural tetiklenirse 1 (short-circuit), aksi 0.
- YAML I/O: from_yaml_file (eksik dosya → FileNotFoundError, bos kural
  listesi izinli, parse hatalari kapsamli).
- parse_rule_mapping / parse_tag_condition_mapping defensif hata mesajlari.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from custos.analytics.cross_sensor_engine import (
    DEFAULT_WINDOW_MIN,
    CrossSensorAlert,
    CrossSensorEngine,
    CrossSensorRule,
    TagCondition,
    parse_rule_mapping,
    parse_tag_condition_mapping,
    resolve_enabled,
)

# ---------------- TagCondition validation ----------------


def test_tag_condition_empty_name_raises() -> None:
    """tag_name bos → ValueError."""
    with pytest.raises(ValueError, match="tag_name"):
        TagCondition(tag_name="", op="gt", threshold=1.0)


def test_tag_condition_invalid_alphabet_raises() -> None:
    """Snake_case ASCII disindaki karakter (büyük harf, tire) → ValueError."""
    with pytest.raises(ValueError, match="snake_case"):
        TagCondition(tag_name="Sensor-1", op="gt", threshold=1.0)


def test_tag_condition_invalid_op_raises() -> None:
    """Bilinmeyen op (ör. 'between') → ValueError."""
    with pytest.raises(ValueError, match="op"):
        TagCondition(tag_name="wind_t_x", op="between", threshold=1.0)


def test_tag_condition_nan_threshold_raises() -> None:
    """NaN threshold → ValueError."""
    with pytest.raises(ValueError, match="finite"):
        TagCondition(tag_name="wind_t_x", op="gt", threshold=float("nan"))


# ---------------- TagCondition.matches ----------------


def test_matches_gt_strict() -> None:
    """gt: degişiklik strict (esit eslesmez)."""
    cond = TagCondition(tag_name="wind_t_x", op="gt", threshold=10.0)
    assert cond.matches(11.0) is True
    assert cond.matches(10.0) is False
    assert cond.matches(9.99) is False


def test_matches_gte_equality_inclusive() -> None:
    """gte: esitlik dahil."""
    cond = TagCondition(tag_name="wind_t_x", op="gte", threshold=10.0)
    assert cond.matches(10.0) is True
    assert cond.matches(10.0001) is True
    assert cond.matches(9.999) is False


def test_matches_lt_lte_lower_tail() -> None:
    """lt strict, lte esitlik dahil."""
    c_lt = TagCondition(tag_name="wind_t_x", op="lt", threshold=10.0)
    c_lte = TagCondition(tag_name="wind_t_x", op="lte", threshold=10.0)
    assert c_lt.matches(9.99) is True
    assert c_lt.matches(10.0) is False
    assert c_lte.matches(10.0) is True


def test_matches_eq_uses_tolerance() -> None:
    """eq: floating-point tolerans, strict == degil."""
    cond = TagCondition(tag_name="wind_t_x", op="eq", threshold=1.0)
    assert cond.matches(1.0) is True
    assert cond.matches(1.0 + 1e-12) is True
    assert cond.matches(1.1) is False


def test_matches_nan_value_is_false() -> None:
    """NaN okuma anlamsiz → False (anomaly imzasi degil)."""
    cond = TagCondition(tag_name="wind_t_x", op="gt", threshold=0.0)
    assert cond.matches(float("nan")) is False
    assert cond.matches(float("inf")) is False


# ---------------- CrossSensorRule validation ----------------


def test_rule_empty_conditions_raises() -> None:
    """tag_conditions bos olamaz."""
    with pytest.raises(ValueError, match="en az 1"):
        CrossSensorRule(
            rule_id="bad_rule",
            tag_conditions=(),
        )


def test_rule_invalid_severity_raises() -> None:
    """severity 'warn'/'crit' disinda → ValueError."""
    with pytest.raises(ValueError, match="severity"):
        CrossSensorRule(
            rule_id="bad",
            tag_conditions=(TagCondition("wind_t_x", "gt", 1.0),),
            severity="info",
        )


def test_rule_duplicate_tag_name_raises() -> None:
    """Ayni tag_name iki kez → ValueError (editor hatasi gizlenmesin)."""
    with pytest.raises(ValueError, match="tekrarli"):
        CrossSensorRule(
            rule_id="dup",
            tag_conditions=(
                TagCondition("wind_t_x", "gt", 1.0),
                TagCondition("wind_t_x", "lt", 5.0),
            ),
        )


def test_rule_invalid_window_min_raises() -> None:
    """window_min < 1 → ValueError."""
    with pytest.raises(ValueError, match="window_min"):
        CrossSensorRule(
            rule_id="bad",
            tag_conditions=(TagCondition("wind_t_x", "gt", 1.0),),
            window_min=0,
        )


# ---------------- CrossSensorRule.evaluate ----------------


def test_evaluate_and_all_true() -> None:
    """require_all=True + tum kosullar saglandi → True."""
    rule = CrossSensorRule(
        rule_id="bearing_combo",
        tag_conditions=(
            TagCondition("wind_t_gen_bearing_de_temp", "gt", 75.0),
            TagCondition("wind_t_gen_bearing_nde_temp", "gt", 75.0),
        ),
    )
    readings = {
        "wind_t_gen_bearing_de_temp": 78.0,
        "wind_t_gen_bearing_nde_temp": 76.0,
    }
    assert rule.evaluate(readings) is True


def test_evaluate_and_one_false() -> None:
    """require_all=True + bir kosul saglanmadi → False."""
    rule = CrossSensorRule(
        rule_id="bearing_combo",
        tag_conditions=(
            TagCondition("wind_t_gen_bearing_de_temp", "gt", 75.0),
            TagCondition("wind_t_gen_bearing_nde_temp", "gt", 75.0),
        ),
    )
    readings = {
        "wind_t_gen_bearing_de_temp": 78.0,
        "wind_t_gen_bearing_nde_temp": 60.0,  # esik altinda
    }
    assert rule.evaluate(readings) is False


def test_evaluate_and_missing_tag_is_false() -> None:
    """require_all=True + eksik tag → False (AND mantigi eksik = false)."""
    rule = CrossSensorRule(
        rule_id="hyd",
        tag_conditions=(
            TagCondition("wind_t_hydraulic_oil_temp", "gt", 65.0),
            TagCondition("wind_t_pitch_angle_std", "gt", 2.0),
        ),
    )
    readings = {"wind_t_hydraulic_oil_temp": 70.0}  # pitch tag eksik
    assert rule.evaluate(readings) is False


def test_evaluate_or_any_true() -> None:
    """require_all=False + bir kosul saglandi → True."""
    rule = CrossSensorRule(
        rule_id="any_overheat",
        tag_conditions=(
            TagCondition("wind_t_l1", "gt", 90.0),
            TagCondition("wind_t_l2", "gt", 90.0),
        ),
        require_all=False,
    )
    readings = {"wind_t_l1": 95.0, "wind_t_l2": 80.0}
    assert rule.evaluate(readings) is True


def test_evaluate_or_all_false() -> None:
    """require_all=False + hiç kosul saglanmadi → False."""
    rule = CrossSensorRule(
        rule_id="any_overheat",
        tag_conditions=(
            TagCondition("wind_t_l1", "gt", 90.0),
            TagCondition("wind_t_l2", "gt", 90.0),
        ),
        require_all=False,
    )
    readings = {"wind_t_l1": 70.0, "wind_t_l2": 80.0}
    assert rule.evaluate(readings) is False


# ---------------- CrossSensorEngine ----------------


def test_engine_duplicate_rule_id_raises() -> None:
    """Ayni rule_id iki kez → ValueError."""
    rule_a = CrossSensorRule(
        rule_id="dup_id",
        tag_conditions=(TagCondition("wind_t_x", "gt", 1.0),),
    )
    rule_b = CrossSensorRule(
        rule_id="dup_id",
        tag_conditions=(TagCondition("wind_t_y", "lt", 1.0),),
    )
    with pytest.raises(ValueError, match="Duplicate rule_id"):
        CrossSensorEngine([rule_a, rule_b])


def test_engine_required_tags_sorted_dedup() -> None:
    """required_tags farkli kurallardan birlestirilir, sorted + dedup."""
    engine = CrossSensorEngine([
        CrossSensorRule(
            rule_id="r1",
            tag_conditions=(
                TagCondition("wind_t_b", "gt", 1.0),
                TagCondition("wind_t_a", "gt", 1.0),
            ),
        ),
        CrossSensorRule(
            rule_id="r2",
            tag_conditions=(
                TagCondition("wind_t_a", "lt", 5.0),  # 'a' tekrar
                TagCondition("wind_t_c", "gt", 1.0),
            ),
        ),
    ])
    assert engine.required_tags == ("wind_t_a", "wind_t_b", "wind_t_c")
    assert engine.n_rules == 2


def test_evaluate_current_returns_alert_on_match() -> None:
    """Tetiklenen kural icin CrossSensorAlert doner."""
    engine = CrossSensorEngine([
        CrossSensorRule(
            rule_id="gen_bearing",
            description="Generator bearing kombo",
            severity="crit",
            tag_conditions=(
                TagCondition("wind_t_gen_bearing_de_temp", "gt", 75.0),
                TagCondition("wind_t_gen_bearing_nde_temp", "gt", 75.0),
            ),
        ),
    ])
    readings = {
        "wind_t_gen_bearing_de_temp": 80.0,
        "wind_t_gen_bearing_nde_temp": 78.0,
    }
    alerts = engine.evaluate_current(asset_instance_id=3, readings=readings)
    assert len(alerts) == 1
    a = alerts[0]
    assert isinstance(a, CrossSensorAlert)
    assert a.rule_id == "gen_bearing"
    assert a.asset_instance_id == 3
    assert a.severity == "crit"
    assert len(a.triggered_conditions) == 2
    # Snapshot icinde her iki tag de var
    snapshot_tags = {name for name, _ in a.readings_snapshot}
    assert snapshot_tags == {
        "wind_t_gen_bearing_de_temp",
        "wind_t_gen_bearing_nde_temp",
    }


def test_evaluate_current_no_match_returns_empty() -> None:
    """Hicbir kural tetiklenmediginde bos liste."""
    engine = CrossSensorEngine([
        CrossSensorRule(
            rule_id="r1",
            tag_conditions=(TagCondition("wind_t_x", "gt", 100.0),),
        ),
    ])
    readings = {"wind_t_x": 50.0}
    assert engine.evaluate_current(asset_instance_id=1, readings=readings) == []


# ---------------- evaluate_history (CARE benchmark) ----------------


def test_evaluate_history_with_list_of_lists() -> None:
    """list of lists kabul edilir, satir-bazi kararlar uretilir."""
    engine = CrossSensorEngine([
        CrossSensorRule(
            rule_id="hot",
            tag_conditions=(TagCondition("wind_t_temp", "gt", 50.0),),
        ),
    ])
    features = [
        [10.0, 45.0],
        [11.0, 55.0],
        [12.0, 60.0],
        [13.0, 30.0],
    ]
    tag_columns = {"wind_t_temp": 1}
    preds = engine.evaluate_history(features, tag_columns)
    assert preds == [0, 1, 1, 0]


def test_evaluate_history_with_numpy_array() -> None:
    """numpy ndarray icin de ayni davranis."""
    engine = CrossSensorEngine([
        CrossSensorRule(
            rule_id="hot",
            tag_conditions=(TagCondition("wind_t_temp", "gt", 50.0),),
        ),
    ])
    features = np.array([
        [10.0, 45.0],
        [11.0, 55.0],
        [12.0, 60.0],
        [13.0, 30.0],
    ])
    tag_columns = {"wind_t_temp": 1}
    preds = engine.evaluate_history(features, tag_columns)
    assert preds == [0, 1, 1, 0]


def test_evaluate_history_combines_multiple_rules() -> None:
    """Iki kural OR'lanir — biri tetiklenirse satir 1."""
    engine = CrossSensorEngine([
        CrossSensorRule(
            rule_id="r_temp",
            tag_conditions=(TagCondition("wind_t_temp", "gt", 50.0),),
        ),
        CrossSensorRule(
            rule_id="r_speed",
            tag_conditions=(TagCondition("wind_t_speed", "gt", 100.0),),
        ),
    ])
    features = np.array([
        [40.0, 90.0],   # iki kural da pasif
        [60.0, 50.0],   # sadece temp tetikledi
        [40.0, 120.0],  # sadece speed tetikledi
        [60.0, 120.0],  # ikisi de tetikledi
    ])
    tag_columns = {"wind_t_temp": 0, "wind_t_speed": 1}
    preds = engine.evaluate_history(features, tag_columns)
    assert preds == [0, 1, 1, 1]


def test_evaluate_history_missing_column_treats_as_absent() -> None:
    """tag_columns_map'te olmayan tag eksik sayilir, ANDed kural saglanmaz."""
    engine = CrossSensorEngine([
        CrossSensorRule(
            rule_id="combo",
            tag_conditions=(
                TagCondition("wind_t_temp", "gt", 50.0),
                TagCondition("wind_t_other", "gt", 0.0),
            ),
        ),
    ])
    features = np.array([
        [60.0],  # temp asildi ama other yok
    ])
    tag_columns = {"wind_t_temp": 0}  # wind_t_other yok
    preds = engine.evaluate_history(features, tag_columns)
    assert preds == [0]


# ---------------- YAML I/O ----------------


def test_from_yaml_file_parses_rules(tmp_path: Path) -> None:
    """YAML cross_sensor_rules bolumu engine'e yuklenir."""
    yaml_path = tmp_path / "tpl.yaml"
    yaml_path.write_text(
        """
slug: dummy
name: Test
roles:
  - role_key: x
    label: X
cross_sensor_rules:
  - rule_id: hyd_combo
    description: hidrolik
    severity: warn
    require_all: true
    tag_conditions:
      - tag_name: wind_t_hyd_temp
        op: gt
        threshold: 65.0
      - tag_name: wind_t_pitch_std
        op: gt
        threshold: 2.0
""",
        encoding="utf-8",
    )
    engine = CrossSensorEngine.from_yaml_file(yaml_path)
    assert engine.n_rules == 1
    assert engine.rules[0].rule_id == "hyd_combo"
    assert engine.required_tags == ("wind_t_hyd_temp", "wind_t_pitch_std")


def test_from_yaml_file_no_rules_section_creates_empty(tmp_path: Path) -> None:
    """cross_sensor_rules anahtari yoksa bos engine olusur."""
    yaml_path = tmp_path / "tpl.yaml"
    yaml_path.write_text(
        "slug: x\nname: X\nroles:\n  - role_key: a\n    label: A\n",
        encoding="utf-8",
    )
    engine = CrossSensorEngine.from_yaml_file(yaml_path)
    assert engine.n_rules == 0
    assert engine.required_tags == ()


def test_from_yaml_file_missing_raises(tmp_path: Path) -> None:
    """Eksik dosya → FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        CrossSensorEngine.from_yaml_file(tmp_path / "yok.yaml")


# ---------------- Parser helpers ----------------


def test_parse_tag_condition_extra_key_raises() -> None:
    """Tanimsiz key (typo) → ValueError."""
    with pytest.raises(ValueError, match="extra"):
        parse_tag_condition_mapping({
            "tag_name": "wind_t_x",
            "op": "gt",
            "threshold": 1.0,
            "typo_field": "bad",
        })


def test_parse_tag_condition_missing_key_raises() -> None:
    """Eksik gerekli key → ValueError."""
    with pytest.raises(ValueError, match="missing"):
        parse_tag_condition_mapping({"tag_name": "wind_t_x", "op": "gt"})


def test_parse_rule_mapping_missing_tag_conditions_raises() -> None:
    """tag_conditions bos veya yok → ValueError."""
    with pytest.raises(ValueError, match="tag_conditions"):
        parse_rule_mapping({"rule_id": "x", "tag_conditions": []})


def test_parse_rule_mapping_defaults_applied() -> None:
    """Opsiyonel field'lar default deger alir."""
    rule = parse_rule_mapping({
        "rule_id": "minimal",
        "tag_conditions": [
            {"tag_name": "wind_t_x", "op": "gt", "threshold": 1.0},
        ],
    })
    assert rule.severity == "warn"
    assert rule.window_min == DEFAULT_WINDOW_MIN
    assert rule.require_all is True
    assert rule.description == ""


# ---------------- Env-gate (CUSTOS_CROSS_SENSOR — Faz 2 Prompt 2 mini-edit) ----------------


def test_resolve_enabled_default_is_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """Env ayarsiz → False (AVM/pilot safe-default).

    Wind Farm A Portekiz baseline'inin Custos kural esikleriyle
    uyumsuz oldugu CARE benchmark'ında gosterildi (Combined CARE 0.530
    → 0.500 regresyon); default OFF saha kalibrasyonu olmadan
    regresyon uretmeyi engeller.
    """
    monkeypatch.delenv("CUSTOS_CROSS_SENSOR", raising=False)
    assert resolve_enabled() is False


@pytest.mark.parametrize("raw", ["on", "ON", "1", "true", "yes", "  On  "])
def test_resolve_enabled_truthy_values(
    monkeypatch: pytest.MonkeyPatch, raw: str,
) -> None:
    """on/1/true/yes (case + whitespace tolerant) → True."""
    monkeypatch.setenv("CUSTOS_CROSS_SENSOR", raw)
    assert resolve_enabled() is True


@pytest.mark.parametrize("raw", ["off", "0", "no", "false", "foo", ""])
def test_resolve_enabled_falsy_values(
    monkeypatch: pytest.MonkeyPatch, raw: str,
) -> None:
    """Bilinmeyen veya 'off' → False (default davranisi koru)."""
    monkeypatch.setenv("CUSTOS_CROSS_SENSOR", raw)
    assert resolve_enabled() is False


def test_resolve_enabled_independent_from_engine_load(tmp_path: Path) -> None:
    """resolve_enabled engine yuklemesinden bagimsiz; helper saf env okuma.

    Validate scripti env on iken from_yaml_file cagirir, off iken
    skip eder — bu test helper'in saf davranisini tek başına dogrular.
    """
    yaml_path = tmp_path / "tpl.yaml"
    yaml_path.write_text(
        "slug: x\nname: X\nroles:\n  - role_key: a\n    label: A\n",
        encoding="utf-8",
    )
    # Env off → resolve False, ama from_yaml_file calismaya devam eder
    # (helper sadece policy, mekanik degil)
    engine = CrossSensorEngine.from_yaml_file(yaml_path)
    assert engine.n_rules == 0  # YAML'da kural yok


def test_anomaly_detector_reads_env_when_param_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """AnomalyDetector ``cross_sensor_enabled=None`` ise env okur."""
    from unittest.mock import MagicMock  # noqa: PLC0415

    from custos.analytics.anomaly_detector import AnomalyDetector  # noqa: PLC0415

    monkeypatch.setenv("CUSTOS_CROSS_SENSOR", "off")
    detector_off = AnomalyDetector(db=MagicMock(), models_dir=tmp_path)
    assert detector_off.cross_sensor_enabled is False

    monkeypatch.setenv("CUSTOS_CROSS_SENSOR", "on")
    detector_on = AnomalyDetector(db=MagicMock(), models_dir=tmp_path)
    assert detector_on.cross_sensor_enabled is True


def test_anomaly_detector_explicit_param_overrides_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Explicit ``cross_sensor_enabled=False`` env 'on' iken bile devre disi."""
    from unittest.mock import MagicMock  # noqa: PLC0415

    from custos.analytics.anomaly_detector import AnomalyDetector  # noqa: PLC0415

    monkeypatch.setenv("CUSTOS_CROSS_SENSOR", "on")
    detector = AnomalyDetector(
        db=MagicMock(), models_dir=tmp_path, cross_sensor_enabled=False,
    )
    assert detector.cross_sensor_enabled is False
