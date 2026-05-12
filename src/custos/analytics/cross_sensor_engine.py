"""Cross-sensor multi-tag rule engine (Wind pivot Faz 2 Prompt 2).

Wind pivot Faz 2 Prompt 2 (2026-05-12). Hidrolik grubunun 6 event'inden 5'i
Faz 1.5'te geç tespit edildi cunku **tek sensör threshold cross**
algoritmasi sadece bir sensoru izliyor (basinc VEYA temperature VEYA
vibration); birlikte degerlendirme yok. Bu modul ``hydraulic_oil_temp >
75 AND pitch_angle_std > 2`` gibi ANDed cok-sensör kurallarini
saniyelerde degerlendirir ve operator-gorunur alarm cikarir.

NOT: ``custos.shared.database.CrossSensorRule`` (AVM legacy F3.5 / V11-305)
tek tag-vs-tag karsilastirmasi yapar (``tag_a operator tag_b``). Bu modul
**multi-tag scalar threshold AND** semantigi ile farklidir; isim cakismasi
icin kasitli ayri tutuldu (AVM DB sema'sina dokunmuyoruz).

YAML semantigi (asset template ``cross_sensor_rules:`` bolumu)
-------------------------------------------------------------
::

    cross_sensor_rules:
      - rule_id: hyd_anomaly_1
        description: "Hidrolik basinc + pitch instability"
        severity: warn
        window_min: 30
        require_all: true       # AND (default). false → OR.
        tag_conditions:
          - tag_name: wind_t_hydraulic_oil_temp
            op: gt
            threshold: 75.0
          - tag_name: wind_t_pitch_angle_std
            op: gt
            threshold: 2.0

Calistirma yollari
------------------
1. **Online**: ``CrossSensorEngine.evaluate_current(asset_id, readings)``
   asset'in son okumalarini alir, kosullari kontrol eder. Online inference
   yolu ``anomaly_detector.py`` veya ayri bir loop'tan cagrilir.
2. **Offline (CARE validation)**: ``evaluate_history(features_matrix,
   tag_columns)`` dataset matrix'inden tum tick'leri tarayip 0/1
   prediction array uretir; ``validate_models_on_care.py`` bunu
   ``predictions_cross_sensor`` engine'inde kullanir.

Operator gerekceleri
--------------------
- ``window_min``: AND'lenen kosullarin ayni 30 dk pencerede true olmasi
  arandi. AVM legacy cross_sensor anlik tick'i karsilastiriyor — wind
  pivot icin pencere kavrami SCADA 10 dk aggregate semantigine uyumlu
  (3 ardisik tick).
- ``severity``: 'warn' default; 'crit' yalnizca kritik ariza imzasi
  iceren kurallarda (CLAUDE feedback_alarm_critical_policy ile uyumlu —
  cross_sensor user-defined oldugundan crit izinli).
- ``require_all=False``: OR semantigi acilabilir; pratikte hep AND. Test
  edilebilirlik icin parametre.
"""

from __future__ import annotations

import dataclasses
import math
import os
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Final

import numpy as np
import structlog
import yaml

if TYPE_CHECKING:
    from numpy.typing import NDArray

logger = structlog.get_logger(logger_name="cross_sensor_engine")

# Master switch env var — AVM safe-default 'off'. Wind .env.wind'inde
# pilot saha kalibrasyonu sonrasi 'on' yapilir. Faz 2 Prompt 2
# benchmark'ında Wind Farm A (Portekiz iklim) baseline'inin
# kurallarla uyumsuz oldugu (Combined CARE 0.530 → 0.500 regresyon)
# gosterildiginden, default OFF — saha kalibrasyonu olmadan engine
# aktif edilmez (CUSTOS_TREND_MONITOR + CUSTOS_PER_ASSET_THRESHOLD ile
# ayni pattern).
_ENABLED_ENV: Final[str] = "CUSTOS_CROSS_SENSOR"
_ENABLED_VALUES: Final[frozenset[str]] = frozenset({"on", "1", "true", "yes"})


def resolve_enabled() -> bool:
    """``CUSTOS_CROSS_SENSOR`` env var → bool (default False — AVM/pilot safe).

    Module-level helper; ``anomaly_detector`` ve
    ``validate_models_on_care`` ayni env semantigini paylasir.
    Test fixture'lari bunu monkeypatch eder.
    """
    raw = os.environ.get(_ENABLED_ENV, "off").strip().lower()
    return raw in _ENABLED_VALUES

# Karsilastirma operatorleri — Fraunhofer rule semantigi.
# AVM legacy 'neq' destekliyor ama biz "anomaly imzasi" icin gerekli
# minimum kume ile yetinmek istiyoruz: gt/gte/lt/lte/eq.
_VALID_OPS: Final[frozenset[str]] = frozenset({"gt", "gte", "lt", "lte", "eq"})

# Default pencere (dk). 30 dk = 3 SCADA tick (10 dk aggregate).
DEFAULT_WINDOW_MIN: Final[int] = 30

# Default severity. user-defined cross_sensor kural — crit izinli (operator
# politikasiyla uyumlu).
DEFAULT_SEVERITY: Final[str] = "warn"
_VALID_SEVERITIES: Final[frozenset[str]] = frozenset({"warn", "crit"})

# Tag adi alfabesi — seed_wind_tags.py konvansiyonu (snake_case + ASCII).
_TAG_NAME_ALPHABET: Final[frozenset[str]] = frozenset(
    "abcdefghijklmnopqrstuvwxyz0123456789_",
)

# Eq tolerans — floating point esitlik. Pratikte cross-sensor'da eq nadirdir
# (genellikle gt/lt) ama YAML'da kullanildiginda strict float == yanlistir.
_EQ_TOL: Final[float] = 1e-9


@dataclasses.dataclass(frozen=True)
class TagCondition:
    """Tek bir tag uzerinde scalar threshold kosulu.

    ``tag_name`` Custos tag adi (seed_wind_tags.py 'wind_t_*' veya AVM
    konvansiyonu). ``op`` ``_VALID_OPS`` arasindan; ``threshold`` scalar
    karsilastirma degeri.

    Field validasyonu ``__post_init__``'te erken yakalanir — YAML
    yuklemesi sirasinda hata satirla birlikte cikar.
    """

    tag_name: str
    op: str
    threshold: float

    def __post_init__(self) -> None:
        """Field dogrulama — gec hata vermek yerine kuruluş sirasinda yakala."""
        if not self.tag_name:
            msg = "tag_name bos olamaz"
            raise ValueError(msg)
        if any(c not in _TAG_NAME_ALPHABET for c in self.tag_name):
            msg = (
                f"tag_name snake_case ASCII olmali (a-z 0-9 _): "
                f"{self.tag_name!r}"
            )
            raise ValueError(msg)
        if self.op not in _VALID_OPS:
            msg = (
                f"op {sorted(_VALID_OPS)} arasinda olmali, geldi: {self.op!r}"
            )
            raise ValueError(msg)
        if not math.isfinite(self.threshold):
            msg = (
                f"threshold finite olmali (NaN/inf yasak), geldi: "
                f"{self.threshold}"
            )
            raise ValueError(msg)

    def matches(self, value: float) -> bool:
        """``value`` bu kosulu sagliyor mu? NaN değer → False (anlamsiz)."""
        if not math.isfinite(value):
            return False
        if self.op == "gt":
            return value > self.threshold
        if self.op == "gte":
            return value >= self.threshold
        if self.op == "lt":
            return value < self.threshold
        if self.op == "lte":
            return value <= self.threshold
        # eq — float tolerans
        return abs(value - self.threshold) <= _EQ_TOL


@dataclasses.dataclass(frozen=True)
class CrossSensorRule:
    """Cok-sensor scalar threshold kurali (wind pivot anlami).

    NOT: ``custos.shared.database.CrossSensorRule`` AVM legacy tag_a-op-tag_b
    karsilastirmasidir; bu sinif onu **degil** YAML-tabanli multi-tag
    threshold AND/OR mantigini ifade eder.

    Tasarim::

        rule = CrossSensorRule(
            rule_id="hyd_anomaly_1",
            tag_conditions=[
                TagCondition("wind_t_hydraulic_oil_temp", "gt", 75.0),
                TagCondition("wind_t_pitch_angle_std",    "gt",  2.0),
            ],
            window_min=30,
            require_all=True,
            severity="warn",
            description="Hidrolik + pitch instability",
        )

    ``window_min`` su an "kurali deglerlendirme penceresi" olarak metadata;
    online inference'de caller ayni 30 dk pencerede mi karar verir (her
    kosulun son N tick agregesi tutulur). Offline (CARE) tek-tick semantik:
    her satir bagimsiz degerlendirilir, window_min yalnizca log/raporlama.
    """

    rule_id: str
    tag_conditions: tuple[TagCondition, ...]
    window_min: int = DEFAULT_WINDOW_MIN
    require_all: bool = True
    severity: str = DEFAULT_SEVERITY
    description: str = ""

    def __post_init__(self) -> None:
        """Field dogrulama — early-fail."""
        if not self.rule_id:
            msg = "rule_id bos olamaz"
            raise ValueError(msg)
        if any(c not in _TAG_NAME_ALPHABET for c in self.rule_id):
            msg = (
                f"rule_id snake_case ASCII olmali (a-z 0-9 _): "
                f"{self.rule_id!r}"
            )
            raise ValueError(msg)
        if not self.tag_conditions:
            msg = "tag_conditions en az 1 kosul icermeli"
            raise ValueError(msg)
        if self.window_min < 1:
            msg = (
                f"window_min en az 1 dakika olmali, geldi: {self.window_min}"
            )
            raise ValueError(msg)
        if self.severity not in _VALID_SEVERITIES:
            msg = (
                f"severity {sorted(_VALID_SEVERITIES)} arasinda olmali, "
                f"geldi: {self.severity!r}"
            )
            raise ValueError(msg)
        # Duplicate tag_name kontrolu — ayni tagi iki kez kontrol etmek
        # mantikli olsa da YAML editor hatasi gizler; uyariyi early yapalim.
        seen_tags: set[str] = set()
        for cond in self.tag_conditions:
            if cond.tag_name in seen_tags:
                msg = (
                    f"rule_id={self.rule_id!r} tag_conditions'da tekrarli "
                    f"tag_name: {cond.tag_name!r}"
                )
                raise ValueError(msg)
            seen_tags.add(cond.tag_name)

    @property
    def required_tag_names(self) -> tuple[str, ...]:
        """Kuralin baktigi tag_name'ler — caller readings dict'i hazirlamak icin."""
        return tuple(c.tag_name for c in self.tag_conditions)

    def evaluate(self, readings: dict[str, float]) -> bool:
        """``readings`` dict'inde kosul saglandi mi?

        ``readings`` her tag_name → son okuma. Eksik tag (key yok):
        - ``require_all=True``: AND mantigi → eksik tag = saglanmiyor → False.
        - ``require_all=False``: OR mantigi → eksik tag atlanir, geri kalan
          herhangi biri saglanirsa True; hicbiri yoksa False.

        NaN/inf değerler ``TagCondition.matches`` icinde False sayilir.
        """
        matches: list[bool] = []
        for cond in self.tag_conditions:
            if cond.tag_name not in readings:
                if self.require_all:
                    return False
                # OR modunda eksik tag atlanir (False sayilir, ama tek True
                # yeterli oldugu icin etkisi tek tek kontrol edilince ortaya cikar).
                matches.append(False)
                continue
            matches.append(cond.matches(readings[cond.tag_name]))
        if self.require_all:
            return all(matches)
        return any(matches)


@dataclasses.dataclass(frozen=True)
class CrossSensorAlert:
    """Engine'in urettigi alert kaydi (immutable, log/DB icin).

    ``triggered_conditions`` hangi kosullarin asildigini saklar — operator
    "neden alarm verdi" sorgusu icin onemli. ``readings_snapshot`` o anki
    tag → value haritasi (audit + post-mortem).
    """

    rule_id: str
    asset_instance_id: int
    severity: str
    description: str
    triggered_conditions: tuple[str, ...]
    readings_snapshot: tuple[tuple[str, float], ...]


class CrossSensorEngine:
    """YAML-tabanli multi-tag cross-sensor rule degerlendirici.

    Yukleme yollari::

        engine = CrossSensorEngine.from_yaml_file(template_path)
        # veya
        engine = CrossSensorEngine.from_rules(rules)

    Online inference::

        readings = {"wind_t_hydraulic_oil_temp": 78.2,
                    "wind_t_pitch_angle_std":     2.7}
        alerts = engine.evaluate_current(asset_instance_id=1,
                                         readings=readings)
        for a in alerts:
            # alarm_events / log / push
            ...

    Offline CARE benchmark::

        # CARE dataset features (n_rows, n_features) + kolon adlari
        preds = engine.evaluate_history(features, tag_columns_map)

    Multi-rule semantigi: tum kurallar bagimsiz degerlendirilir; biri
    tetiklendiginde diger kurallar suskunlasmaz (her arıza imzasi kendi
    operator gerekcesini tasir).
    """

    def __init__(self, rules: list[CrossSensorRule]) -> None:
        """Kurallari memory'e alir.

        Duplicate rule_id YASAK — YAML editor hatasi gizlemeyelim.
        """
        seen_ids: set[str] = set()
        for r in rules:
            if r.rule_id in seen_ids:
                msg = f"Duplicate rule_id: {r.rule_id!r}"
                raise ValueError(msg)
            seen_ids.add(r.rule_id)
        self._rules: tuple[CrossSensorRule, ...] = tuple(rules)
        # required tag isimlerini global kume olarak topla — readings
        # hazirlama icin caller'a ipucu.
        tag_names: set[str] = set()
        for r in self._rules:
            tag_names.update(r.required_tag_names)
        self._required_tags: tuple[str, ...] = tuple(sorted(tag_names))

    # --- Factory helpers ---

    @classmethod
    def from_rules(cls, rules: list[CrossSensorRule]) -> CrossSensorEngine:
        """Direct constructor — testler icin shortcut."""
        return cls(rules)

    @classmethod
    def from_yaml_file(cls, path: Path) -> CrossSensorEngine:
        """Tek bir asset template YAML'indan cross_sensor_rules okur.

        Eksik dosya → ``FileNotFoundError``. YAML kok dict olmali, aksi
        halde ``ValueError``. ``cross_sensor_rules`` anahtari yoksa engine
        bos kural listesiyle olusur (no-op davranis).
        """
        if not path.is_file():
            msg = f"Asset template dosyasi bulunamadi: {path}"
            raise FileNotFoundError(msg)
        raw = path.read_text(encoding="utf-8")
        data = yaml.safe_load(raw)
        if not isinstance(data, dict):
            msg = (
                f"Template kok dict olmali (bulundu: {type(data).__name__})"
            )
            raise ValueError(msg)
        rules_raw = data.get("cross_sensor_rules", []) or []
        rules = [parse_rule_mapping(item) for item in rules_raw]
        return cls(rules)

    @classmethod
    def from_yaml_dir(cls, directory: Path) -> CrossSensorEngine:
        """Klasördeki tum .yaml dosyalarinin cross_sensor_rules'larini birlestirir.

        Birden cok template oldugunda kural birlesimi yapar; duplicate
        rule_id tum dosyalar arasinda yasak.
        """
        if not directory.is_dir():
            msg = f"Template dizini bulunamadi: {directory}"
            raise FileNotFoundError(msg)
        combined: list[CrossSensorRule] = []
        for yaml_path in sorted(directory.glob("*.yaml")):
            raw = yaml_path.read_text(encoding="utf-8")
            data = yaml.safe_load(raw)
            if not isinstance(data, dict):
                continue
            rules_raw = data.get("cross_sensor_rules", []) or []
            for item in rules_raw:
                combined.append(parse_rule_mapping(item))
        return cls(combined)

    # --- Public properties ---

    @property
    def rules(self) -> tuple[CrossSensorRule, ...]:
        """Yuklu kurallar (immutable kopya)."""
        return self._rules

    @property
    def required_tags(self) -> tuple[str, ...]:
        """Tum kurallarin baktigi tag_name'ler (sorted, dedup).

        Caller bunu tag binding sorgusu hazirlarken kullanir — sadece
        gerekli tag'lar son okuma cache'inde tutulur.
        """
        return self._required_tags

    @property
    def n_rules(self) -> int:
        """Kural sayisi (debug + log icin)."""
        return len(self._rules)

    # --- Online evaluation ---

    def evaluate_current(
        self,
        asset_instance_id: int,
        readings: dict[str, float],
    ) -> list[CrossSensorAlert]:
        """``readings`` snapshot'i uzerinden tum kurallari deglerlendirir.

        Tetiklenen her kural icin bir ``CrossSensorAlert`` doner;
        tetiklenmeyen kurallar sessiz. Bos kural listesi → bos liste.
        """
        alerts: list[CrossSensorAlert] = []
        for rule in self._rules:
            if not rule.evaluate(readings):
                continue
            triggered = tuple(
                f"{c.tag_name}{c.op}{c.threshold}"
                for c in rule.tag_conditions
                if c.tag_name in readings and c.matches(readings[c.tag_name])
            )
            snapshot = tuple(
                (name, float(readings[name]))
                for name in rule.required_tag_names
                if name in readings
            )
            alerts.append(
                CrossSensorAlert(
                    rule_id=rule.rule_id,
                    asset_instance_id=asset_instance_id,
                    severity=rule.severity,
                    description=rule.description,
                    triggered_conditions=triggered,
                    readings_snapshot=snapshot,
                ),
            )
        return alerts

    # --- Offline CARE evaluation ---

    def evaluate_history(
        self,
        features: NDArray[np.float64] | Sequence[Sequence[float]],
        tag_columns_map: dict[str, int],
    ) -> list[int]:
        """CARE benchmark icin row-bazinda 0/1 prediction array uretir.

        ``features`` (n_rows, n_features) matris — numpy ndarray, list of
        list veya tuple of tuple kabul. ``tag_columns_map`` tag_name →
        kolon indeksi. Map'te bulunmayan tag'lar her satirda eksik sayilir
        (kural ANDed ise False'a duser).

        Donus: uzunlugu n_rows olan ``list[int]``; herhangi bir kural
        tetiklenen satir 1, aksi 0. Caller numpy array'e cevirir.
        """
        arr = np.asarray(features, dtype=np.float64)
        if arr.ndim != 2:
            msg = (
                f"features 2D matris olmali (n_rows, n_features), "
                f"geldi: shape={arr.shape}"
            )
            raise ValueError(msg)
        n_rows = int(arr.shape[0])
        n_cols = int(arr.shape[1])
        preds: list[int] = [0] * n_rows
        for i in range(n_rows):
            readings = {
                tag: float(arr[i, idx])
                for tag, idx in tag_columns_map.items()
                if 0 <= idx < n_cols
            }
            for rule in self._rules:
                if rule.evaluate(readings):
                    preds[i] = 1
                    # Bir kural yetti — diger kurallari kontrol etmeye gerek yok.
                    break
        return preds


# --- YAML parser helpers (modul-seviye) ---


def parse_tag_condition_mapping(item: object) -> TagCondition:
    """YAML mapping'inden ``TagCondition`` olusturur.

    Beklenen keyler: ``tag_name``, ``op``, ``threshold``. Eksik/fazla
    key → ``ValueError`` (template editor hatasi gizlenmesin).
    """
    if not isinstance(item, dict):
        msg = (
            f"tag_condition mapping olmali (bulundu: {type(item).__name__})"
        )
        raise ValueError(msg)
    expected = {"tag_name", "op", "threshold"}
    actual = set(item.keys())
    extra = actual - expected
    missing = expected - actual
    if extra or missing:
        msg = (
            f"tag_condition key uyumsuz: extra={sorted(extra)}, "
            f"missing={sorted(missing)}"
        )
        raise ValueError(msg)
    return TagCondition(
        tag_name=str(item["tag_name"]),
        op=str(item["op"]).strip().lower(),
        threshold=float(item["threshold"]),
    )


def parse_rule_mapping(item: object) -> CrossSensorRule:
    """YAML mapping'inden ``CrossSensorRule`` olusturur.

    Beklenen keyler: ``rule_id``, ``tag_conditions`` (zorunlu);
    ``description``, ``severity``, ``window_min``, ``require_all`` opsiyonel.
    Tanimsiz keyler reddedilir (typo'lari yakalamak icin).
    """
    if not isinstance(item, dict):
        msg = f"rule mapping olmali (bulundu: {type(item).__name__})"
        raise ValueError(msg)
    allowed = {
        "rule_id",
        "tag_conditions",
        "description",
        "severity",
        "window_min",
        "require_all",
    }
    actual = set(item.keys())
    extra = actual - allowed
    if extra:
        msg = f"rule key tanimsiz: extra={sorted(extra)}"
        raise ValueError(msg)
    if "rule_id" not in item:
        msg = "rule.rule_id zorunlu"
        raise ValueError(msg)
    if "tag_conditions" not in item or not item["tag_conditions"]:
        msg = "rule.tag_conditions zorunlu ve en az 1 eleman"
        raise ValueError(msg)
    conds = tuple(
        parse_tag_condition_mapping(c) for c in item["tag_conditions"]
    )
    return CrossSensorRule(
        rule_id=str(item["rule_id"]),
        tag_conditions=conds,
        window_min=int(item.get("window_min", DEFAULT_WINDOW_MIN)),
        require_all=bool(item.get("require_all", True)),
        severity=str(item.get("severity", DEFAULT_SEVERITY)).strip().lower(),
        description=str(item.get("description", "")),
    )


__all__ = [
    "DEFAULT_SEVERITY",
    "DEFAULT_WINDOW_MIN",
    "CrossSensorAlert",
    "CrossSensorEngine",
    "CrossSensorRule",
    "TagCondition",
    "parse_rule_mapping",
    "parse_tag_condition_mapping",
    "resolve_enabled",
]
