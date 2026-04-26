"""Anomali tespit modülü — Isolation Forest tabanlı.

İki bileşen:
- train_model_for_instance(): Offline eğitim — tag reading'lerinden
  Isolation Forest modeli eğitir, .joblib dosyasına yazar.
- AnomalyDetector: Analytics loop'ta periyodik çalışan inference —
  eğitilmiş modelleri yükler, son tag değerlerinden skor hesaplar.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import structlog

from custos.shared.database import (
    AnomalyScore,
    AuditLogEntry,
    DatabaseInterface,
)

logger = structlog.get_logger(logger_name="anomaly_detector")

# Minimum eğitim satırı — bundan az veri varsa model eğitilmez
_MIN_TRAINING_ROWS = 10


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
    ) -> None:
        self._db = db
        self._models_dir = models_dir
        self._interval = interval_seconds
        self._task: asyncio.Task[None] | None = None
        self._running = False
        # Yüklenmiş modeller: instance_id → model
        self._models: dict[int, object] = {}

    async def start(self) -> None:
        """Detector'ı başlatır — arka plan task olarak çalışır."""
        self._running = True
        self._load_models()
        await logger.ainfo(
            "Anomaly detector başlatıldı",
            interval=self._interval,
            models_loaded=len(self._models),
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
        """Model dosyalarını diskten yükler."""
        import joblib  # noqa: PLC0415 — lazy import

        self._models.clear()
        if not self._models_dir.exists():
            return

        for path in self._models_dir.glob("anomaly_*.joblib"):
            try:
                # Dosya adından instance_id çıkar: anomaly_{id}.joblib
                instance_id = int(path.stem.split("_")[1])
                self._models[instance_id] = joblib.load(path)
            except (ValueError, IndexError, Exception):
                logger.warning(
                    "Model dosyası yüklenemedi",
                    path=str(path),
                )

    async def _detect_cycle(self) -> None:
        """Tek bir tespit döngüsü — her instance için anomali skoru hesaplar."""
        import numpy as np  # noqa: PLC0415

        if not self._models:
            return

        instances = await self._db.list_asset_instances(status="active")
        now = datetime.now(UTC)
        anomaly_count = 0

        for instance in instances:
            assert instance.id is not None
            model = self._models.get(instance.id)
            if model is None:
                continue

            # Tag binding → son değerler
            bindings = await self._db.list_tag_bindings(instance.id)
            if not bindings:
                continue

            tag_ids = [b.tag_id for b in bindings]
            readings = await self._db.get_latest_tag_readings(tag_ids)

            # Feature vektörü oluştur (binding sırasıyla)
            feature_values: list[float] = []
            for binding in bindings:
                reading = readings.get(binding.tag_id)
                if reading is None:
                    break
                feature_values.append(reading.value)
            else:
                # Tüm tag'lerden değer alındı — skor hesapla
                feature_array = np.array([feature_values])
                prediction = model.predict(feature_array)  # type: ignore[attr-defined]
                raw_scores = model.score_samples(feature_array)  # type: ignore[attr-defined]
                score = float(raw_scores[0])
                is_anomaly = bool(prediction[0] == -1)

                feature_json = json.dumps(
                    {tid: v for tid, v in zip(tag_ids, feature_values, strict=True)},
                )

                await self._db.insert_anomaly_score(
                    AnomalyScore(
                        instance_id=instance.id,
                        timestamp=now,
                        score=score,
                        is_anomaly=is_anomaly,
                        feature_vector=feature_json,
                    )
                )

                if is_anomaly:
                    anomaly_count += 1
                    await self._db.insert_audit_log(
                        AuditLogEntry(
                            category="anomaly",
                            action="detected",
                            entity_type="asset_instance",
                            entity_id=str(instance.id),
                            detail=f"Anomali skoru: {score:.4f}",
                        )
                    )

        if anomaly_count > 0:
            await logger.awarn(
                "Anomali tespit edildi",
                count=anomaly_count,
            )
