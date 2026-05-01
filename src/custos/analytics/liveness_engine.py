"""Stuck-at + Counter Liveness Engine — Layer 1 kural (V11-108, P-05).

Sensör donma yakalama. Threshold engine yalnızca üst/alt eşik aşımını
tespit eder; sensör donar (kablo kopması, transmitter arızası, prob
donması) ise eski değer "normal" görünür ve threshold tetiklenmez.
Bu modül 30 saniyede bir aktif tag'leri tarayıp şu iki durumu yakalar:

- **Stuck-at**: Son okunan değer ile son farklı değer arasındaki süre
  preset/override eşiğini aşıyorsa "donmuş" alarmı.
- **Counter**: ``stuck_at_preset='counter'`` (sayaç tag'ı) için iki ek
  mantık: değer azaldıysa (sayaç geri gitti) veya N saniyedir hiç
  artmadıysa.

K11 hibrit yaklaşımı — bu modül **Layer 1**. Layer 3 ML personalize
(P-12) 2-3 hafta veri sonrası ``analytics/liveness_ml.py`` aynı ALARM
API'siyle ML adaptif eşik kullanacak.

Bakım modu (P-04) entegrasyonu: alarm yazımından önce per-instance ve
global bakım kontrol edilir; aktifse ``is_test=true`` flag'iyle yazılır,
push gönderilmez.

Cooldown: aynı tag için 1 saat içinde tekrar alarm üretmez (iki tarafı
da temizledikten sonra yeni alarm açılır).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Final

import structlog

from custos.analytics import maintenance_mode
from custos.analytics.push_sender import send_push_notifications
from custos.shared.database import (
    AlarmEvent,
    DatabaseInterface,
    TagReading,
    TagRecord,
)
from custos.shared.stuck_at_presets import (
    resolve_effective_preset,
    resolve_stuck_at_seconds,
)

logger = structlog.get_logger(logger_name="liveness_engine")

# Tick aralığı — 30 saniye (V11-108). Tag sayısı yüzlerce olsa bile
# query_tag_readings/2 saat penceresi hızlıdır (TimescaleDB hypertable).
_TICK_INTERVAL_SECONDS: Final[float] = 30.0

# Lookback penceresi — son 2 saatte tag değişti mi? Çok yavaş tag'ler
# için (very_slow=1 saat eşiği) yeterli; daha uzunluk gerek yok.
_LOOKBACK_HOURS: Final[int] = 2

# Cooldown — aynı tag için 1 saat içinde tekrar alarm açılmaz. Kullanıcı
# alarmı acknowledge etmese bile hourly tekrar gürültüsü yok.
_COOLDOWN: Final[timedelta] = timedelta(hours=1)

# Counter rollover heuristic — Modbus uint16 sayaçları (kWh, m³ vb.)
# 65535'e ulaşınca 0'a sarar (rollover). Pencerenin başı yüksek (>60000)
# ve sonu düşük (<5000) ise sahte alarm yerine rollover kabul edilir.
# Eşikler güvenlik payı bırakır: pencere içinde sayaç ~5500 birim daha
# arttıktan sonra rollover'ı yakalayan testler bile çalışır, bu arada
# 30000 → 28000 gibi gerçek geri-gitmeleri yakalamaya devam eder.
_COUNTER_ROLLOVER_HIGH_THRESHOLD: Final[float] = 60000.0
_COUNTER_ROLLOVER_LOW_THRESHOLD: Final[float] = 5000.0


class LivenessEngine:
    """Stuck-at + counter mode kural-bazlı sensör donma tespiti.

    Threshold engine ile aynı arka plan task deseni: ``start()`` döngüsü
    kendi tick periyoduyla çalışır, hatalar yutulur ve loglanır,
    ``CancelledError`` ile temiz çıkış.
    """

    def __init__(
        self,
        db: DatabaseInterface,
        tick_interval_seconds: float = _TICK_INTERVAL_SECONDS,
        cooldown: timedelta = _COOLDOWN,
        lookback_hours: int = _LOOKBACK_HOURS,
    ) -> None:
        self._db = db
        self._tick_interval = tick_interval_seconds
        self._cooldown = cooldown
        self._lookback_hours = lookback_hours
        self._running = False
        # tag_id → son alarm tetikleme zamanı (cooldown takibi).
        self._cooldown_tracker: dict[str, datetime] = {}

    async def start(self) -> None:
        """Engine'i başlatır — arka plan task olarak çalışır."""
        self._running = True
        await logger.ainfo(
            "Liveness engine başlatıldı",
            tick_interval=self._tick_interval,
            lookback_hours=self._lookback_hours,
        )
        try:
            while self._running:
                try:
                    await self._tick()
                except Exception:
                    await logger.aerror(
                        "Liveness tick hatası",
                        exc_info=True,
                    )
                await asyncio.sleep(self._tick_interval)
        except asyncio.CancelledError:
            await logger.ainfo("Liveness engine iptal edildi")

    async def stop(self) -> None:
        """Engine'i durdurur."""
        self._running = False
        await logger.ainfo("Liveness engine durduruldu")

    async def _tick(self) -> None:
        """Tek tarama: tüm aktif tag'leri kontrol eder."""
        now = datetime.now(UTC)
        tags = await self._db.list_tags(status="active")
        if not tags:
            return

        # P-04 bakım modu — tag→instance haritası (threshold_engine ile aynı
        # cache pattern). Tek query, per-tick tazelenir; binding'ler nadir
        # değişir ama orada-değil-burada-değil olmasın.
        bindings = await self._db.list_tag_bindings_all()
        tag_instance_map: dict[str, int | None] = {
            b.tag_id: b.instance_id for b in bindings
        }

        # Global bakım modu — ucuz tek sorgu; aktifse tüm tag'lerde is_test
        # işaretlenir, alarmlar yine yazılır (P-12 eğitim setinden filtre).
        global_test = await maintenance_mode.is_global_maintenance(
            self._db, now,
        )

        for tag in tags:
            seconds = resolve_stuck_at_seconds(tag)
            if seconds is None:
                continue

            readings = await self._db.query_tag_readings(
                tag.tag_id,
                start=now - timedelta(hours=self._lookback_hours),
                end=now,
            )
            if len(readings) < 2:
                # Yeterli veri yok — yeni eklenmiş tag, hiç polling olmamış.
                continue

            preset = resolve_effective_preset(tag)
            if preset == "counter":
                message = await _check_counter(readings, seconds, tag.tag_id)
            else:
                message = _check_stuck_at(readings, seconds, now)

            if message is None:
                continue

            # Cooldown — aynı tag için 1 saat içinde tekrar atlama.
            last = self._cooldown_tracker.get(tag.tag_id)
            if last is not None and (now - last) < self._cooldown:
                continue

            # Bakım modu kontrolü — global zaten biliniyor; per-instance
            # binding varsa ek kontrol.
            is_test = global_test
            if not is_test:
                instance_id = tag_instance_map.get(tag.tag_id)
                if instance_id is not None:
                    is_test = await maintenance_mode.is_instance_in_maintenance(
                        self._db, instance_id, now,
                    )

            await self._raise_alarm(tag, message, now, is_test)
            self._cooldown_tracker[tag.tag_id] = now

    async def _raise_alarm(
        self,
        tag: TagRecord,
        message: str,
        now: datetime,
        is_test: bool,
    ) -> None:
        """Liveness alarm event'i oluşturur ve push'u tetikler."""
        await self._db.insert_alarm_event(
            AlarmEvent(
                tag_id=tag.tag_id,
                threshold_id=None,
                state="triggered",
                triggered_at=now,
                trigger_value=0.0,
                is_test=is_test,
                source="liveness",
                severity="warn",
                message=message,
            ),
        )
        await logger.awarning(
            "Stuck-at alarm",
            tag_id=tag.tag_id,
            message=message,
            is_test=is_test,
        )

        # Push bildirim — threshold_engine ile aynı yaklaşım. Bakım modunda
        # is_test=True ise send_push_notifications zaten atlar (P-04).
        try:
            await send_push_notifications(
                db=self._db,
                title=f"Custos Liveness: {tag.name}",
                body=f"Tag {tag.tag_id}: {message}",
                severity="warn",
                is_test=is_test,
            )
        except Exception:
            await logger.awarning(
                "Liveness push bildirim gönderilemedi",
                tag_id=tag.tag_id,
                exc_info=True,
            )


def _check_stuck_at(
    readings: list[TagReading],
    seconds: int,
    now: datetime,
) -> str | None:
    """Son okunan değer ne kadar zamandır aynı? Eşiği aştıysa alarm.

    Sondan başa doğru tarayıp ``last_value`` ile aynı kalan en eski
    okumanın timestamp'ini bulur — bu, "sensör son ne zamandan beri bu
    değere takılı" sorusunun cevabı. ``now - bu_zaman > seconds`` ise
    alarm. Tüm pencere aynı değerse en eski okuma kullanılır (sensör en
    azından pencere boyu kadar süredir donmuş).
    """
    last_value = readings[-1].value
    last_change_at = readings[-1].timestamp
    for reading in reversed(readings[:-1]):
        if reading.value != last_value:
            break
        # Aynı değer hâlâ tutuluyor; başlangıcı geriye taşı.
        last_change_at = reading.timestamp

    last_change_seconds = (now - last_change_at).total_seconds()
    if last_change_seconds > seconds:
        return (
            f"Sensör donuk: {int(last_change_seconds)}s'dir değer "
            f"değişmedi (eşik: {seconds}s)"
        )
    return None


async def _check_counter(
    readings: list[TagReading],
    seconds: int,
    tag_id: str,
) -> str | None:
    """Counter tag (sayaç) için iki kural birden:

    1. Son değer ilk değerden küçükse → sayaç geri gitti. Modbus uint16
       sayaçlarında 65535 → 0 sarması (rollover) protokolde normal
       davranıştır; ``first_value > 60000`` ve ``last_value < 5000`` ise
       rollover kabul edilir, alarm yok (sadece debug log).
    2. Son değer ilk değere eşitse ve pencere ``seconds``'i aştıysa →
       sayaç durağan, beklenen artış yok.
    """
    first_value = readings[0].value
    last_value = readings[-1].value

    if last_value < first_value:
        # uint16 rollover'ı sahte alarmdan ayır.
        if (
            first_value > _COUNTER_ROLLOVER_HIGH_THRESHOLD
            and last_value < _COUNTER_ROLLOVER_LOW_THRESHOLD
        ):
            await logger.adebug(
                "Counter rollover kabul edildi",
                tag_id=tag_id,
                from_value=first_value,
                to_value=last_value,
            )
            return None
        return f"Counter geri gitti: {first_value:.2f} → {last_value:.2f}"

    if last_value == first_value:
        duration = (
            readings[-1].timestamp - readings[0].timestamp
        ).total_seconds()
        if duration > seconds:
            return (
                f"Counter durağan: {int(duration)}s'dir artmıyor "
                f"(eşik: {seconds}s)"
            )
    return None
