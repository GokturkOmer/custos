"""SPC iskelet — Statistical Process Control streaming motoru (R-07 / V11-308).

Tag basina EWMA + CUSUM + MAD-score uc istatistik. 5 dk tick'le aktif
``spc_enabled`` tag'lerin son okumasini alir, state'i diske yazar; ilk 100
ornek (sessiz) ogrenme penceresi tamamlandiktan sonra sapma alarmlarini
``source='spc'``, severity='warn' yazar (cooldown 30 dk).

Algoritmalar:

- **EWMA** (Exponentially Weighted Moving Average): ``ewma_t = alpha * x_t +
  (1 - alpha) * ewma_{t-1}`` (alpha=0.2). Variance da ayni alpha ile EWMA
  uzerinden takip edilir. Alarm: ``|x_t - ewma_t| > 3 * sqrt(variance)``.
- **CUSUM** (Cumulative Sum): donmus median referansi + 1.4826 * MAD'den
  turetilen sigma. ``cusum_pos = max(0, cusum_pos + (x - median) - K*sigma)``,
  ``cusum_neg = min(0, ...)``. Alarm: ``|cusum| > H * sigma`` (H=5).
- **MAD-score** (robust z-score): donmus median + MAD ile
  ``|x_t - median| / (1.4826 * MAD) > 3.5`` ise alarm. Outlier'a dayanikli.

Donmus median + MAD ogrenme penceresinin sonunda hesaplanir; sonrasinda
baseline degismez (drift'i CUSUM ve EWMA yakalar). Restart'ta:
``learning_complete=True`` ise baseline diskten yuklenir; aksi takdirde
state sifirlanip ogrenme yeniden baslar (in-memory buffer kayboldugu icin).

Threshold engine + escalation pattern'leri ile ayni arka plan task tasarimi:
``start()`` periyodik tick, hatalar yutulur ve loglanir, ``CancelledError``
ile temiz cikis. Push + audit log + bakim modu saygisi ortak yardimcilar
uzerinden.

Kanonik kaynaklar:

- ``shared/database.py`` — SpcState dataclass + get_spc_state /
  upsert_spc_state / list_spc_states + alarm_events.source enum.
- ``analytics/threshold_engine.py`` — ``_raise_layer1_alarm`` deseni
  (R-06); SPC alarmi da ayni yolu takip eder.
- ``alembic/versions/037_mode_aware_spc.py`` — schema.
"""

from __future__ import annotations

import asyncio
import math
from datetime import UTC, datetime, timedelta
from typing import Final

import structlog

from custos.analytics import maintenance_mode
from custos.analytics.push_sender import send_push_notifications
from custos.shared.database import (
    AlarmEvent,
    AuditLogEntry,
    DatabaseInterface,
    SpcState,
)

logger = structlog.get_logger(logger_name="spc_engine")


# --- Yapilandirma sabitleri ---

# Tick aralığı — 5 dk. Pilot ölçeğinde 5 dk'da 1-30 örnek tipik
# (polling 10 sn-5 dk arasi). Daha kisa tick degerli degil; SPC zaten
# yavas drift için.
_TICK_INTERVAL_SECONDS: Final[float] = 300.0

# Ogrenme penceresi — 100 ornek sonrasi median + MAD donar.
_LEARNING_SAMPLES: Final[int] = 100

# EWMA smoothing factor — 0.2 ortalama agirlik son 5-10 ornekte.
_EWMA_ALPHA: Final[float] = 0.2

# CUSUM slack (K) ve esik (H) — ders kitabi varsayilanlari.
# K=0.5: kucuk dalgalanmalar yutulur. H=5: kumulatif sapma 5 sigma'ya ulasinca alarm.
_CUSUM_K: Final[float] = 0.5
_CUSUM_H: Final[float] = 5.0

# MAD-score esiği — 3.5 robust z-score (klasik outlier sınırı).
_MAD_THRESHOLD: Final[float] = 3.5

# Cooldown — ayni tag icin 30 dk icinde tekrar alarm yazilmaz.
_COOLDOWN: Final[timedelta] = timedelta(minutes=30)

# Sayisal hata onlemek icin kucuk pozitif epsilon (sigma=0 durumunda).
_EPSILON: Final[float] = 1e-9


def _median(values: list[float]) -> float:
    """Sorted listenin medianini doner. Bos liste 0 doner (defansif)."""
    n = len(values)
    if n == 0:
        return 0.0
    sorted_vals = sorted(values)
    mid = n // 2
    if n % 2 == 1:
        return sorted_vals[mid]
    return (sorted_vals[mid - 1] + sorted_vals[mid]) / 2.0


def _update_ewma(
    state: SpcState,
    value: float,
) -> None:
    """EWMA mean + variance'i in-place gunceller (R-07 / V11-308)."""
    if state.ewma_value is None:
        # Ilk ornek — variance 0'dan baslar, ilk birkac ornek esnasinda buyur.
        state.ewma_value = value
        state.ewma_variance = 0.0
        return
    prev_ewma = state.ewma_value
    state.ewma_value = _EWMA_ALPHA * value + (1.0 - _EWMA_ALPHA) * prev_ewma
    prev_variance = state.ewma_variance or 0.0
    # EWMA variance: alpha * (x - prev_mean)^2 + (1-alpha) * prev_variance
    state.ewma_variance = (
        _EWMA_ALPHA * (value - prev_ewma) ** 2
        + (1.0 - _EWMA_ALPHA) * prev_variance
    )


def _update_cusum(
    state: SpcState,
    value: float,
) -> None:
    """CUSUM pozitif/negatif kumulatif sapmayi in-place gunceller.

    Donmus ``mad_median`` referansi ve ``1.4826 * mad_value`` sigma'si ile
    calistir. Ogrenme tamamlanmamissa hicbir sey yapma (None'lar).
    """
    if state.mad_median is None or state.mad_value is None:
        return
    sigma = max(1.4826 * state.mad_value, _EPSILON)
    deviation = value - state.mad_median
    slack = _CUSUM_K * sigma
    state.cusum_pos = max(0.0, state.cusum_pos + deviation - slack)
    state.cusum_neg = min(0.0, state.cusum_neg + deviation + slack)


def _check_alarm(state: SpcState, value: float) -> tuple[str, str] | None:
    """Ogrenme tamamlandiktan sonra 3 testten biri tetiklerse mesaj doner.

    Donus: ``(alarm_kind, message)`` veya ``None``. ``alarm_kind`` audit
    log icin (ewma/cusum/mad); UI'a gosterilen mesaj da iceride hazirdir.
    Test sirasi: en hassas (MAD) → en az hassas (CUSUM). Hepsi bagimsiz
    test, ilk eslesen alarmi yazariz (gurultu engellenir).
    """
    if not state.learning_complete:
        return None
    if state.mad_median is None or state.mad_value is None:
        return None  # Defansif — learning_complete=True ise dolu olmali.

    # 1. MAD-score (robust z-score) — outlier'a en duyarli.
    mad = max(state.mad_value, _EPSILON)
    z_score = abs(value - state.mad_median) / (1.4826 * mad)
    if z_score > _MAD_THRESHOLD:
        return (
            "mad",
            (
                f"MAD-score {z_score:.2f} (esik {_MAD_THRESHOLD}); "
                f"deger={value:.4f}, baseline median={state.mad_median:.4f}"
            ),
        )

    # 2. CUSUM — yavas drift icin.
    sigma = max(1.4826 * mad, _EPSILON)
    h_limit = _CUSUM_H * sigma
    if state.cusum_pos > h_limit:
        return (
            "cusum",
            (
                f"CUSUM pozitif {state.cusum_pos:.4f} > esik {h_limit:.4f}; "
                f"baseline median={state.mad_median:.4f}'den uzun sure yukarida"
            ),
        )
    if abs(state.cusum_neg) > h_limit:
        return (
            "cusum",
            (
                f"CUSUM negatif {state.cusum_neg:.4f} esik {-h_limit:.4f}'i asti; "
                f"baseline median={state.mad_median:.4f}'den uzun sure asagida"
            ),
        )

    # 3. EWMA 3-sigma testi — anlik buyuk sapma.
    if state.ewma_value is not None and state.ewma_variance is not None:
        ewma_std = math.sqrt(state.ewma_variance)
        if ewma_std > _EPSILON and abs(value - state.ewma_value) > 3.0 * ewma_std:
            return (
                "ewma",
                (
                    f"EWMA {value:.4f} baseline {state.ewma_value:.4f}'den "
                    f"3 sigma sapmis (sigma={ewma_std:.4f})"
                ),
            )

    return None


class SPCEngine:
    """SPC streaming engine — 5 dk tick, EWMA + CUSUM + MAD-score.

    Threshold engine ve escalation loop ile ayni arka plan task pattern'i:
    ``start()`` periyodik tick, hatalar yutulur ve loglanir, ``stop()``
    ile flag indirir, ``CancelledError`` ile temiz cikis.

    Ogrenme penceresi 100 ornek (sessiz). Sonrasinda median + MAD donar
    ve sapma alarmlari ``source='spc'``, severity='warn' yazilmaya baslar.
    Cooldown per-tag 30 dk.

    Bakim modu (per-instance + global) saygi: ``is_test=True`` flag'i ile
    yazilir, push gonderilmez (threshold_engine ile ayni yol).
    """

    _TICK_INTERVAL = _TICK_INTERVAL_SECONDS
    _LEARNING_SAMPLES = _LEARNING_SAMPLES
    _EWMA_ALPHA = _EWMA_ALPHA
    _CUSUM_K = _CUSUM_K
    _CUSUM_H = _CUSUM_H
    _MAD_THRESHOLD = _MAD_THRESHOLD
    _COOLDOWN = _COOLDOWN

    def __init__(
        self,
        db: DatabaseInterface,
        tick_interval_seconds: float = _TICK_INTERVAL_SECONDS,
    ) -> None:
        self._db = db
        self._tick_interval = tick_interval_seconds
        self._running = False
        # In-memory: ogrenme penceresi sirasinda bicrikilen ham deger buffer'i.
        # Restart'ta kaybolur — bu durumda warm-up sample_count'i sifirlar.
        self._learning_buffer: dict[str, list[float]] = {}
        # Ayni timestamp'i tekrar islememek icin (tag polling'i tick'ten
        # daha sik calistigi icin gereksiz; yine de defansif).
        self._cooldown: dict[str, datetime] = {}

    async def start(self) -> None:
        """Engine'i baslatir — arka plan task olarak calisir."""
        self._running = True
        await self._warm_up()
        await logger.ainfo(
            "SPC engine baslatildi",
            tick_interval=self._tick_interval,
            learning_samples=self._LEARNING_SAMPLES,
        )
        try:
            while self._running:
                try:
                    await self._tick()
                except Exception:
                    await logger.aerror(
                        "SPC tick hatasi",
                        exc_info=True,
                    )
                try:
                    await asyncio.sleep(self._tick_interval)
                except asyncio.CancelledError:
                    break
        finally:
            await logger.ainfo("SPC engine durdu")

    async def stop(self) -> None:
        """Engine'i durdurur."""
        self._running = False

    async def _warm_up(self) -> None:
        """Restart sonrasi: ``learning_complete=False`` kayitlari sifirla.

        In-memory buffer kayboldugu icin yarim ogrenmeyi devam ettiremeyiz;
        en temiz davranis sample_count'u sifirlayip yeniden baslamak.
        Tamamlanmis baseline'lar (learning_complete=True) korunur.
        """
        states = await self._db.list_spc_states()
        for state in states:
            if state.learning_complete:
                continue
            if state.sample_count == 0:
                continue
            # Sifirla — buffer kaybolduğu icin median/MAD hesabi yapilamaz.
            reset = SpcState(
                tag_id=state.tag_id,
                sample_count=0,
                ewma_value=None,
                ewma_variance=None,
                cusum_pos=0.0,
                cusum_neg=0.0,
                mad_median=None,
                mad_value=None,
                last_sample_at=None,
                learning_complete=False,
            )
            await self._db.upsert_spc_state(reset)
            await logger.ainfo(
                "SPC state sifirlandi (yarim ogrenme kayboldu)",
                tag_id=state.tag_id,
                lost_samples=state.sample_count,
            )

    async def _tick(self) -> None:
        """Tek tarama — aktif spc_enabled tag'leri tarayip state guncelle.

        1. ``spc_enabled=True`` aktif tag listesi
        2. Her tag icin son okumayi al, state'i guncelle
        3. Ogrenme tamamlandiysa alarm testlerini calistir
        4. Cooldown disindaysa alarmi yaz + push
        5. State'i diske yaz
        """
        active_tags = await self._db.list_tags(status="active")
        candidates = [t for t in active_tags if t.spc_enabled]
        if not candidates:
            return

        now = datetime.now(UTC)
        latest = await self._db.get_latest_tag_readings(
            [t.tag_id for t in candidates],
        )

        # Bakim modu cache — global'i tek sorgu, instance'lari tag→instance
        # haritasi uzerinden lazy.
        global_test = await maintenance_mode.is_global_maintenance(
            self._db, now,
        )
        bindings = await self._db.list_tag_bindings_all()
        tag_instance_map = {b.tag_id: b.instance_id for b in bindings}

        for tag in candidates:
            reading = latest.get(tag.tag_id)
            if reading is None:
                continue

            state = await self._db.get_spc_state(tag.tag_id)
            if state is None:
                state = SpcState(tag_id=tag.tag_id)

            # Idempotency: ayni timestamp'i tekrar isleme (tick'ler arasinda
            # yeni okuma gelmediyse state'i bozma).
            if (
                state.last_sample_at is not None
                and reading.timestamp <= state.last_sample_at
            ):
                continue

            self._update_state(state, reading.value)
            state.last_sample_at = reading.timestamp

            # Alarm testi — ogrenme tamamlandiktan sonra.
            if state.learning_complete:
                alarm = _check_alarm(state, reading.value)
                if alarm is not None:
                    last_alarm = self._cooldown.get(tag.tag_id)
                    if last_alarm is None or (now - last_alarm) >= self._COOLDOWN:
                        instance_id = tag_instance_map.get(tag.tag_id)
                        await self._raise_alarm(
                            tag_id=tag.tag_id,
                            tag_name=tag.name,
                            value=reading.value,
                            alarm_kind=alarm[0],
                            message=alarm[1],
                            now=now,
                            global_test=global_test,
                            instance_id=instance_id,
                        )
                        self._cooldown[tag.tag_id] = now

            await self._db.upsert_spc_state(state)

    def _update_state(self, state: SpcState, value: float) -> None:
        """State'in EWMA + (post-learning) CUSUM ve buffer'ini gunceller.

        Ogrenme penceresi sirasinda buffer'a deger eklenir; sample_count
        ``_LEARNING_SAMPLES``'a ulasinca median + MAD hesaplanip donar,
        ``learning_complete=True`` olur, buffer hafizadan silinir.
        """
        state.sample_count += 1

        # EWMA her zaman calisir (ogrenme sirasinda da test sonrasi etmez).
        _update_ewma(state, value)

        if not state.learning_complete:
            buffer = self._learning_buffer.setdefault(state.tag_id, [])
            buffer.append(value)
            if state.sample_count >= self._LEARNING_SAMPLES:
                # Median + MAD donar.
                median = _median(buffer)
                mad = _median([abs(x - median) for x in buffer])
                state.mad_median = median
                state.mad_value = max(mad, _EPSILON)
                state.learning_complete = True
                self._learning_buffer.pop(state.tag_id, None)
            return

        # Ogrenme bitmis — CUSUM gunceller (donmus median referansi).
        _update_cusum(state, value)

    async def _raise_alarm(
        self,
        *,
        tag_id: str,
        tag_name: str,
        value: float,
        alarm_kind: str,
        message: str,
        now: datetime,
        global_test: bool,
        instance_id: int | None,
    ) -> None:
        """SPC alarmini ortak yola yazar — alarm_event + audit + push.

        Bakim modu kontrolu (per-instance + global) — threshold breach
        deseni ile ayni: ``is_test=True`` ise alarm yazilir ama push
        gonderilmez. Source ``'spc'``, severity sabit ``'warn'`` (V11-308
        iskeletinde severity per-tag tuning yok).
        """
        is_test = global_test
        if not is_test and instance_id is not None:
            is_test = await maintenance_mode.is_instance_in_maintenance(
                self._db, instance_id, now,
            )

        await self._db.insert_alarm_event(
            AlarmEvent(
                threshold_id=None,
                tag_id=tag_id,
                state="triggered",
                triggered_at=now,
                trigger_value=value,
                is_test=is_test,
                source="spc",
                severity="warn",
                message=message,
            ),
        )

        if is_test:
            audit_category = "maintenance_test_alarm"
            audit_action = f"spc_{alarm_kind}_test_triggered"
        else:
            audit_category = "alarm"
            audit_action = f"spc_{alarm_kind}_triggered"
        await self._db.insert_audit_log(
            AuditLogEntry(
                category=audit_category,
                action=audit_action,
                entity_type="tag",
                entity_id=tag_id,
                detail=message,
            ),
        )

        await logger.ainfo(
            "SPC alarm",
            tag_id=tag_id,
            alarm_kind=alarm_kind,
            value=value,
            is_test=is_test,
            message=message,
        )

        try:
            await send_push_notifications(
                db=self._db,
                title=f"Custos SPC: {tag_name}",
                body=message,
                severity="warn",
                is_test=is_test,
            )
        except Exception:
            await logger.awarning(
                "SPC push bildirim gonderilemedi",
                tag_id=tag_id,
                exc_info=True,
            )
