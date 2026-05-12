"""Anomali tespit modülü — Isolation Forest + Autoencoder dual-engine.

Üç bileşen:
- train_model_for_instance(): Offline IF eğitim — tag reading'lerinden
  Isolation Forest modeli eğitir, .joblib dosyasına yazar.
- AnomalyDetector: Analytics loop'ta periyodik çalışan inference —
  eğitilmiş modelleri yükler, son tag değerlerinden skor hesaplar.
- Wind pivot Faz 1.3 (2026-05-12): MLPRegressor autoencoder modeli IF ile
  yan yana skorlanır; ``CUSTOS_ANOMALY_ENGINE`` env var'i ile mod seçilir.

Engine mod (env ``CUSTOS_ANOMALY_ENGINE``, default 'both'):
- ``'if'``: Sadece Isolation Forest (geri uyumlu — AVM production).
- ``'ae'``: Sadece autoencoder (wind pivot eval).
- ``'both'``: Her iki engine — her engine kendi engine_type'i ile yazar.

Model dosya adlandirmasi:
- IF: ``data/models/anomaly_<instance_id>.joblib`` (AVM ve wind ortak).
- AE: ``data/models/autoencoder_<instance_id>_wind.joblib`` (sadece wind).
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from custos.analytics.cross_sensor_engine import (
    resolve_enabled as _resolve_cross_sensor_enabled,
)
from custos.analytics.per_asset_threshold import (
    PerAssetThresholdCalibrator,
    is_anomaly_at_threshold,
)
from custos.analytics.trend_monitor import TrendMonitor
from custos.shared.database import (
    ANOMALY_SUPPRESSED_MODES,
    AlarmEvent,
    AnomalyScore,
    AuditLogEntry,
    DatabaseInterface,
    TrendAlert,
)

if TYPE_CHECKING:
    from custos.analytics.autoencoder_engine import AutoencoderAnomalyEngine

logger = structlog.get_logger(logger_name="anomaly_detector")

# Minimum eğitim satırı — bundan az veri varsa model eğitilmez
_MIN_TRAINING_ROWS = 10

# Wind pivot Faz 1.3: engine mod env var.
_ENGINE_MODE_ENV = "CUSTOS_ANOMALY_ENGINE"
_DEFAULT_ENGINE_MODE = "both"
_VALID_ENGINE_MODES: frozenset[str] = frozenset({"if", "ae", "both"})

# Wind pivot Faz 2 P0: trend monitor master switch.
# AVM safe-default: 'off'. Wind .env dosyasinda 'on' yapilir.
_TREND_MONITOR_ENV = "CUSTOS_TREND_MONITOR"
_TREND_ENABLED_VALUES: frozenset[str] = frozenset({"on", "1", "true", "yes"})


def _resolve_trend_monitor_enabled() -> bool:
    """``CUSTOS_TREND_MONITOR`` env var → bool (default False — AVM safe).

    Wind pilotunda env dosyasinda 'on' yapilir; AVM'de sessizce kapali
    kalir. ``trend_alerts`` tablosu sadece ``custos_wind`` DB'sinde
    mevcuttur, AVM'de yazma denemesi exception firlatir — env kontrolu
    bu hatayı engelleyen birincil kademe.
    """
    raw = os.environ.get(_TREND_MONITOR_ENV, "off").strip().lower()
    return raw in _TREND_ENABLED_VALUES


def _resolve_engine_mode() -> str:
    """``CUSTOS_ANOMALY_ENGINE`` env var → 'if' | 'ae' | 'both' (default 'both').

    Gecersiz deger uyari ile default'a fallback eder; environment yanlis
    konfigurasyonda detector durmaz, sadece IF + AE'yi her ikisini de
    deneyerek devam eder.
    """
    raw = os.environ.get(_ENGINE_MODE_ENV, _DEFAULT_ENGINE_MODE).strip().lower()
    if raw not in _VALID_ENGINE_MODES:
        logger.warning(
            "Gecersiz %s=%r; default %r kullanildi",
            _ENGINE_MODE_ENV,
            raw,
            _DEFAULT_ENGINE_MODE,
        )
        return _DEFAULT_ENGINE_MODE
    return raw


async def train_model_for_instance(
    db: DatabaseInterface,
    instance_id: int,
    output_path: Path,
    lookback_hours: int = 24,
) -> bool:
    """Bir asset instance için Isolation Forest modeli eğitir.

    Son `lookback_hours` saatteki tag reading'lerinden feature vektörü
    oluşturur, Isolation Forest fit eder, modeli `.joblib` dosyasına yazar.
    Yeterli veri yoksa False döndürür.
    """
    import joblib  # noqa: PLC0415 — lazy import, sadece eğitimde gerekli
    import numpy as np  # noqa: PLC0415
    from sklearn.ensemble import IsolationForest  # noqa: PLC0415

    instance = await db.get_asset_instance(instance_id)
    if instance is None:
        await logger.awarn("Instance bulunamadı", instance_id=instance_id)
        return False

    tmpl = await db.get_asset_template(instance.template_id)
    if tmpl is None:
        return False

    bindings = await db.list_tag_bindings(instance_id)
    if not bindings:
        await logger.awarn("Tag binding yok", instance_id=instance_id)
        return False

    # Her tag için son N saatteki okumaları çek
    now = datetime.now(UTC)
    start = now - timedelta(hours=lookback_hours)

    tag_ids = [b.tag_id for b in bindings]
    tag_readings_map: dict[str, list[float]] = {tid: [] for tid in tag_ids}

    for tag_id in tag_ids:
        readings = await db.query_tag_readings(tag_id, start, now)
        tag_readings_map[tag_id] = [r.value for r in readings]

    # En kısa seri uzunluğunu bul — hepsini aynı boyuta kırp
    min_len = min(len(v) for v in tag_readings_map.values()) if tag_readings_map else 0
    if min_len < _MIN_TRAINING_ROWS:
        await logger.awarn(
            "Yetersiz eğitim verisi",
            instance_id=instance_id,
            rows=min_len,
        )
        return False

    # Feature matrix: her satır = [tag1_val, tag2_val, ...]
    feature_matrix = np.column_stack([np.array(tag_readings_map[tid][:min_len]) for tid in tag_ids])

    # Isolation Forest eğitimi
    model = IsolationForest(
        n_estimators=100,
        contamination=0.05,
        random_state=42,
    )
    model.fit(feature_matrix)

    # Model dosyasını kaydet
    output_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, output_path)

    await logger.ainfo(
        "Model eğitildi",
        instance_id=instance_id,
        rows=min_len,
        features=len(tag_ids),
        path=str(output_path),
    )
    return True


class AnomalyDetector:
    """Asset instance'lar için anomali tespit modülü.

    Eğitilmiş Isolation Forest modellerini yükler, periyodik olarak
    son tag değerlerinden skor hesaplar.
    """

    def __init__(
        self,
        db: DatabaseInterface,
        models_dir: Path,
        interval_seconds: float = 60.0,
        engine_mode: str | None = None,
        *,
        trend_monitor_enabled: bool | None = None,
        per_asset_calibrator: PerAssetThresholdCalibrator | None = None,
        cross_sensor_enabled: bool | None = None,
    ) -> None:
        """Detector kurulumu.

        ``engine_mode`` None ise env var (``CUSTOS_ANOMALY_ENGINE``) okunur.
        Explicit deger (test fixture'lari icin) override eder.

        Wind pivot Faz 2 P0 — ``trend_monitor_enabled`` None ise
        ``CUSTOS_TREND_MONITOR`` env'i okur (default 'off'). True iken her
        AE skoru sonrasi ``TrendMonitor.update()`` cagrilir; alert
        cikarsa ``trend_alerts`` tablosuna kayit dusulur ve operatore
        ``alarm_events`` (source='trend_monitor') olarak gosterilir.

        Wind pivot Faz 2 Prompt 2 — ``per_asset_calibrator`` None ise
        per-asset threshold devre disi (global engine threshold'u
        kullanilir). Verilirse cache'ten asset+engine threshold okur ve
        ``is_anomaly`` karari bu degerle yapilir (yon konvansiyonu
        ``is_anomaly_at_threshold`` ile uygulanir). Calibrator kendi
        env-gate'ine sahiptir (``CUSTOS_PER_ASSET_THRESHOLD``); disabled
        iken ``get_threshold`` her zaman None doner, mevcut davranis
        bozulmaz.

        Wind pivot Faz 2 Prompt 2 mini-edit (2026-05-12) —
        ``cross_sensor_enabled`` None ise env ``CUSTOS_CROSS_SENSOR``
        okunur (default 'off'). Cross-sensor engine load + evaluate
        sadece True iken yapilir; AVM ve pilot saha kalibrasyonu
        oncesinde 'off' birakilir. CARE benchmark'inda Wind Farm A
        Portekiz iklim baseline'inin Custos kural esikleriyle uyumsuz
        oldugu gosterildi (Combined CARE 0.530 → 0.500 regresyon);
        default OFF bu regresyonu iptal eder, mimari kodda kalir.
        """
        self._db = db
        self._models_dir = models_dir
        self._interval = interval_seconds
        self._task: asyncio.Task[None] | None = None
        self._running = False
        # Yüklenmiş IF modelleri: instance_id → IsolationForest (geri uyumlu).
        self._models: dict[int, object] = {}
        # Wind pivot Faz 1.3: AE modelleri ayri dict'te (model file naming
        # ``autoencoder_<id>_wind.joblib`` AVM IF dosyalariyla cakismaz).
        self._ae_models: dict[int, AutoencoderAnomalyEngine] = {}
        # Engine mode — explicit > env > default
        self._engine_mode = (
            engine_mode.strip().lower()
            if engine_mode is not None
            else _resolve_engine_mode()
        )
        if self._engine_mode not in _VALID_ENGINE_MODES:
            logger.warning(
                "Gecersiz engine_mode=%r; default %r kullanildi",
                self._engine_mode,
                _DEFAULT_ENGINE_MODE,
            )
            self._engine_mode = _DEFAULT_ENGINE_MODE

        # Wind pivot Faz 2 P0 — trend monitor (env-gated)
        self._trend_enabled = (
            trend_monitor_enabled
            if trend_monitor_enabled is not None
            else _resolve_trend_monitor_enabled()
        )
        # Tek shared TrendMonitor — state per-asset dict, asset_instance_id
        # ile bagimsiz. AnomalyDetector start/stop sirasinda yeniden olusturulur.
        self._trend_monitor: TrendMonitor | None = (
            TrendMonitor() if self._trend_enabled else None
        )
        # Wind pivot Faz 2 Prompt 2 — per-asset adaptive threshold.
        # None → davranis degisiklik yok (global engine threshold).
        self._per_asset_calibrator = per_asset_calibrator

        # Wind pivot Faz 2 Prompt 2 mini-edit (2026-05-12) — cross-sensor
        # engine env-gate (default 'off'). Bu detector cross-sensor
        # engine'i henuz online evaluate etmiyor (entegrasyon noktasi
        # ileride eklenecek); flag _detect_cycle'da check edilir ve
        # cross_sensor evaluate'i skip eder. Property test/debug + log
        # icin kullanilir.
        self._cross_sensor_enabled = (
            cross_sensor_enabled
            if cross_sensor_enabled is not None
            else _resolve_cross_sensor_enabled()
        )

    @property
    def models_dir(self) -> Path:
        """Model dosyalarının disk üzerindeki dizini (R-04 — ML hub'tan eğitim/reset için)."""
        return self._models_dir

    @property
    def loaded_models(self) -> dict[int, object]:
        """Bellekteki yüklü IF modelleri (instance_id → model). R-04 ML hub'ı bu sözlüğü
        son skor + model varlığı için sorgular; doğrudan ``_models`` private
        attr'ına dokunmak yerine read-only property üzerinden okur."""
        return self._models

    @property
    def loaded_ae_models(self) -> dict[int, AutoencoderAnomalyEngine]:
        """Bellekteki yüklü autoencoder modelleri (wind pivot Faz 1.3).

        IF ``loaded_models`` ile semantik olarak ayri: AVM production IF
        kullanir, wind asset_instance'lar AE kullanir; ``both`` modunda
        iki dict birlikte populate edilir.
        """
        return self._ae_models

    @property
    def engine_mode(self) -> str:
        """Aktif engine mod ('if' | 'ae' | 'both'). Test + debug icin read-only."""
        return self._engine_mode

    @property
    def trend_monitor_enabled(self) -> bool:
        """Trend monitor aktif mi (env CUSTOS_TREND_MONITOR). Test/debug icin."""
        return self._trend_enabled

    @property
    def trend_monitor(self) -> TrendMonitor | None:
        """Trend monitor instance'i (disabled ise None). Test/debug icin."""
        return self._trend_monitor

    @property
    def per_asset_calibrator(self) -> PerAssetThresholdCalibrator | None:
        """Per-asset threshold calibrator (None ise disabled). Test/debug icin."""
        return self._per_asset_calibrator

    @property
    def cross_sensor_enabled(self) -> bool:
        """Cross-sensor engine master switch (env ``CUSTOS_CROSS_SENSOR``).

        Default False (AVM/pilot safe). Online evaluate entegrasyonu
        eklendiginde bu flag _detect_cycle'da kontrol edilir; False ise
        engine.load_rules() + evaluate skip edilir.
        """
        return self._cross_sensor_enabled

    async def start(self) -> None:
        """Detector'ı başlatır — arka plan task olarak çalışır."""
        self._running = True
        self._load_models()
        # Wind pivot Faz 2 Prompt 2 — per-asset threshold cache'i diskten
        # (DB'den) yukle. Calibrator None ise veya kendi env'i 'off' ise
        # warm_cache no-op (0 doner).
        per_asset_count = 0
        if self._per_asset_calibrator is not None:
            try:
                per_asset_count = await self._per_asset_calibrator.warm_cache()
            except Exception:
                # Migration 042 uygulanmadiysa burada UndefinedTableError
                # gelir; detector durmasin, sadece warn at.
                await logger.aerror(
                    "Per-asset threshold cache yuklenirken hata "
                    "(migration 042 uygulanmis mi?)",
                    exc_info=True,
                )
        await logger.ainfo(
            "Anomaly detector başlatıldı",
            interval=self._interval,
            engine_mode=self._engine_mode,
            if_models_loaded=len(self._models),
            ae_models_loaded=len(self._ae_models),
            trend_monitor_enabled=self._trend_enabled,
            per_asset_threshold_count=per_asset_count,
            cross_sensor_enabled=self._cross_sensor_enabled,
        )
        try:
            while self._running:
                try:
                    await self._detect_cycle()
                except Exception:
                    await logger.aerror(
                        "Anomali tespit döngüsünde hata",
                        exc_info=True,
                    )
                await asyncio.sleep(self._interval)
        except asyncio.CancelledError:
            await logger.ainfo("Anomaly detector iptal edildi")

    async def stop(self) -> None:
        """Detector'ı durdurur."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        await logger.ainfo("Anomaly detector durduruldu")

    def _load_models(self) -> None:
        """Model dosyalarını diskten yükler — IF + AE (engine_mode'a göre).

        - IF modelleri: ``anomaly_<id>.joblib`` (mevcut davranis).
        - AE modelleri: ``autoencoder_<id>_wind.joblib`` (wind pivot Faz 1.3).

        Engine mode 'if' ise sadece IF yuklenir, 'ae' ise sadece AE,
        'both' ise her ikisi. Bozuk dosyalar uyari logu ile atlanir
        (detector durmaz).
        """
        import joblib  # noqa: PLC0415 — lazy import

        self._models.clear()
        self._ae_models.clear()

        if not self._models_dir.exists():
            return

        if self._engine_mode in {"if", "both"}:
            for path in self._models_dir.glob("anomaly_*.joblib"):
                try:
                    # Dosya adından instance_id çıkar: anomaly_{id}.joblib
                    instance_id = int(path.stem.split("_")[1])
                    self._models[instance_id] = joblib.load(path)
                except (ValueError, IndexError, Exception):
                    logger.warning(
                        "IF model dosyası yüklenemedi",
                        path=str(path),
                    )

        if self._engine_mode in {"ae", "both"}:
            # Lazy import — autoencoder_engine sklearn'i lazy yukler,
            # ama burada sinif gerekir; modul import maliyetini sadece
            # AE yuklerken oder.
            from custos.analytics.autoencoder_engine import (  # noqa: PLC0415
                AutoencoderAnomalyEngine,
            )
            for path in self._models_dir.glob("autoencoder_*_wind.joblib"):
                try:
                    # Dosya adi: autoencoder_<id>_wind.joblib
                    # Parts: ['autoencoder', '<id>', 'wind']
                    parts = path.stem.split("_")
                    instance_id = int(parts[1])
                    self._ae_models[instance_id] = AutoencoderAnomalyEngine.load(path)
                except (ValueError, IndexError, Exception):
                    logger.warning(
                        "AE model dosyası yüklenemedi",
                        path=str(path),
                    )

    async def _detect_cycle(self) -> None:
        """Tek bir tespit döngüsü — her instance için anomali skoru hesaplar.

        Wind pivot Faz 1.3: ``engine_mode``a göre IF + AE skorlanir; her
        engine kendi engine_type'i ile ayri AnomalyScore satiri yazar.
        Audit log her anomaly icin tek satir (engine_type detail'de).
        """
        import numpy as np  # noqa: PLC0415

        if not self._models and not self._ae_models:
            return

        # R-04: Sistem-geneli ML inference master switch. False iken
        # tick erken döner, hiçbir instance için inference yapılmaz
        # (push_global_enabled ile aynı desen — singleton retention_config
        # satırında saklanır, kullanıcı ML hub'tan toggle eder).
        config = await self._db.get_retention_config()
        if not config.ml_inference_enabled:
            return

        instances = await self._db.list_asset_instances(status="active")
        now = datetime.now(UTC)
        anomaly_count = 0

        for instance in instances:
            assert instance.id is not None
            # R-04: Per-instance ML toggle — False ise instance atlanır.
            if not instance.ml_enabled:
                continue
            # R-07 (V11-307): Mode-aware iskelet. startup/shutdown modlarinda
            # operator setpoint degistirir/asset hizlanir-yavaslar; bu gecis
            # surecinde anomali bombardimani false positive uretir. Bu modlarda
            # alarm yazimini atliyoruz; running ve idle modlarinda normal devam.
            # Tam mode-conditional model Faz 3 V11-303 ile gelecek; bu paket
            # sadece manuel toggle + alarm yazimi atlamasi.
            if instance.operating_mode in ANOMALY_SUPPRESSED_MODES:
                await logger.adebug(
                    "Mode-aware: alarm yazimi atlandi",
                    instance_id=instance.id,
                    operating_mode=instance.operating_mode,
                )
                continue

            if_model = self._models.get(instance.id)
            ae_model = self._ae_models.get(instance.id)
            if if_model is None and ae_model is None:
                continue

            # Tag binding → son değerler (her iki engine icin ortak)
            bindings = await self._db.list_tag_bindings(instance.id)
            if not bindings:
                continue

            tag_ids = [b.tag_id for b in bindings]
            readings = await self._db.get_latest_tag_readings(tag_ids)

            # Feature vektörü oluştur (binding sırasıyla)
            feature_values: list[float] = []
            missing = False
            for binding in bindings:
                reading = readings.get(binding.tag_id)
                if reading is None:
                    missing = True
                    break
                feature_values.append(reading.value)
            if missing:
                continue

            feature_array = np.array([feature_values])
            feature_json = json.dumps(
                {tid: v for tid, v in zip(tag_ids, feature_values, strict=True)},
            )

            # --- IF engine ---
            if if_model is not None:
                prediction = if_model.predict(feature_array)  # type: ignore[attr-defined]
                raw_scores = if_model.score_samples(feature_array)  # type: ignore[attr-defined]
                if_score = float(raw_scores[0])
                if_is_anomaly = bool(prediction[0] == -1)
                # Wind pivot Faz 2 Prompt 2 — per-asset threshold override
                # (IF: alt kuyruk, score < threshold = anomaly). Cache'te
                # asset+engine entry yoksa global model davranisi korunur.
                if self._per_asset_calibrator is not None:
                    if_threshold = self._per_asset_calibrator.get_threshold(
                        instance.id, "if",
                    )
                    if if_threshold is not None:
                        if_is_anomaly = is_anomaly_at_threshold(
                            if_score, "if", if_threshold,
                        )
                await self._db.insert_anomaly_score(
                    AnomalyScore(
                        instance_id=instance.id,
                        timestamp=now,
                        score=if_score,
                        is_anomaly=if_is_anomaly,
                        feature_vector=feature_json,
                        engine_type="if",
                    )
                )
                if if_is_anomaly:
                    anomaly_count += 1
                    await self._db.insert_audit_log(
                        AuditLogEntry(
                            category="anomaly",
                            action="detected",
                            entity_type="asset_instance",
                            entity_id=str(instance.id),
                            detail=f"IF anomali skoru: {if_score:.4f}",
                        )
                    )

            # --- AE engine (wind pivot Faz 1.3) ---
            if ae_model is not None:
                try:
                    ae_scores = ae_model.score(feature_array)
                    ae_flags = ae_model.is_anomaly(feature_array)
                except ValueError:
                    # Feature sayisi uyumsuz (tag binding degisti?); skip.
                    await logger.awarn(
                        "AE skor hatasi — feature uyumsuzlugu",
                        instance_id=instance.id,
                        n_features=feature_array.shape[1],
                    )
                else:
                    ae_score = float(ae_scores[0])
                    ae_is_anomaly = bool(ae_flags[0])
                    # Wind pivot Faz 2 Prompt 2 — per-asset threshold override
                    # (AE: ust kuyruk, score > threshold = anomaly).
                    if self._per_asset_calibrator is not None:
                        ae_threshold = self._per_asset_calibrator.get_threshold(
                            instance.id, "ae",
                        )
                        if ae_threshold is not None:
                            ae_is_anomaly = is_anomaly_at_threshold(
                                ae_score, "ae", ae_threshold,
                            )
                    await self._db.insert_anomaly_score(
                        AnomalyScore(
                            instance_id=instance.id,
                            timestamp=now,
                            score=ae_score,
                            is_anomaly=ae_is_anomaly,
                            feature_vector=feature_json,
                            engine_type="ae",
                        )
                    )
                    if ae_is_anomaly:
                        anomaly_count += 1
                        await self._db.insert_audit_log(
                            AuditLogEntry(
                                category="anomaly",
                                action="detected",
                                entity_type="asset_instance",
                                entity_id=str(instance.id),
                                detail=f"AE anomali skoru: {ae_score:.4f}",
                            )
                        )
                    # Wind pivot Faz 2 P0 — trend monitor (env-gated)
                    if self._trend_monitor is not None:
                        await self._check_trend_alert(
                            instance.id, now, ae_score, bindings[0].tag_id,
                        )

        if anomaly_count > 0:
            await logger.awarn(
                "Anomali tespit edildi",
                count=anomaly_count,
            )

    async def _check_trend_alert(
        self,
        instance_id: int,
        timestamp: datetime,
        ae_score: float,
        first_tag_id: str,
    ) -> None:
        """AE skoru sonrasi trend monitor tetiklemesi.

        Faz 2 P0: AE reconstruction error (RMSE) ham skoruna EWMA-slope
        analizi uygular. Slope esigi asilirsa:
        1. ``trend_alerts`` tablosuna ayrintili kayit (slope, duration).
        2. ``alarm_events`` tablosuna operator-gorunur satir (source=
           'trend_monitor'). ``first_tag_id`` instance'in ilk binding
           tag'i — alarm sayfasinin tag filtresinde gostermek icin.

        DB exception (ornegin ``custos`` AVM DB'sinde ``trend_alerts``
        tablo yok) ile karsilasildiginda hata yutulur ve uyari loglanir;
        detector durmaz. Best-practice: env ``CUSTOS_TREND_MONITOR``
        kontrolu yapildi (init'te), bu fallback sadece migration
        eksikligi gibi sapmalar icin.
        """
        assert self._trend_monitor is not None
        alert = self._trend_monitor.update(instance_id, timestamp, ae_score)
        if alert is None:
            return

        try:
            await self._db.insert_trend_alert(
                TrendAlert(
                    asset_instance_id=alert.asset_instance_id,
                    timestamp=alert.timestamp,
                    current_score=alert.current_score,
                    ewma_slope=alert.ewma_slope,
                    duration_min=alert.duration_min,
                    severity=alert.severity,
                ),
            )
        except Exception:
            await logger.aerror(
                "trend_alerts INSERT hatasi (migration 041 uygulanmis mi?)",
                instance_id=instance_id,
                exc_info=True,
            )

        # alarm_events'e de yaz — operator alarm sayfasinda gorur.
        # ``first_tag_id`` zorunlu kolon icin (anchor): trend asset-level
        # ama UI tag-bazli gosterim yapar.
        try:
            await self._db.insert_alarm_event(
                AlarmEvent(
                    tag_id=first_tag_id,
                    triggered_at=alert.timestamp,
                    trigger_value=alert.ewma_slope,
                    source="trend_monitor",
                    severity=alert.severity,
                    message=(
                        f"Trend slope {alert.ewma_slope:.6f}/tick "
                        f"({alert.duration_min} dk surdu)"
                    ),
                ),
            )
        except Exception:
            await logger.aerror(
                "alarm_events INSERT hatasi (trend monitor)",
                instance_id=instance_id,
                exc_info=True,
            )

        await self._db.insert_audit_log(
            AuditLogEntry(
                category="anomaly",
                action="trend_detected",
                entity_type="asset_instance",
                entity_id=str(instance_id),
                detail=(
                    f"Trend slope={alert.ewma_slope:.6f} "
                    f"duration={alert.duration_min}dk sev={alert.severity}"
                ),
            ),
        )
