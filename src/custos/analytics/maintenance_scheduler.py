"""Periyodik bakım zamanlayıcısı — due schedule'ları task'a dönüştürür.

Analytics sürecinde arka plan task olarak çalışır. Her tick'te
(varsayılan 5 dakika):

1. Vadesi gelmiş aktif schedule'ları çeker (next_due_at <= now).
2. Her schedule için ilgili asset'lere (instance veya template-bazlı
   tüm instance'lar) maintenance_task (source='schedule') kaydı açar.
3. Schedule'ın next_due_at'ini period_kind'a göre ileri iter.
4. Belirli bir toleransı geçmiş pending task'ları 'missed' olarak
   işaretler (MISSED_THRESHOLD_HOURS).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import structlog

from custos.shared.database import (
    DatabaseInterface,
    MaintenanceSchedule,
    MaintenanceTask,
)
from custos.shared.maintenance_periods import compute_next_due_at

logger = structlog.get_logger(logger_name="maintenance_scheduler")

# Scheduler tick periyodu — bakım görevleri için saniye hassasiyeti
# gerekmez; 5 dakika hem anlık hem sistem yüküne dostça.
DEFAULT_TICK_SECONDS = 300

# Bir task'ın due_at'ini geçtikten sonra 'pending' kalabileceği üst sınır.
# Bunu aşan pending task'lar 'missed' olarak işaretlenir.
MISSED_THRESHOLD_HOURS = 48


class MaintenanceScheduler:
    """Asyncio tabanlı bakım zamanlayıcısı."""

    def __init__(
        self,
        db: DatabaseInterface,
        tick_seconds: int = DEFAULT_TICK_SECONDS,
    ) -> None:
        self._db = db
        self._tick_seconds = tick_seconds
        self._running = False
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Arka plan döngüsü — process süresince çalışır."""
        self._running = True
        await logger.ainfo(
            "Maintenance scheduler başlatıldı",
            tick_seconds=self._tick_seconds,
        )
        try:
            while self._running:
                try:
                    await self.run_once()
                except Exception:
                    await logger.aerror(
                        "Scheduler tick hatası",
                        exc_info=True,
                    )
                await asyncio.sleep(self._tick_seconds)
        except asyncio.CancelledError:
            await logger.ainfo("Maintenance scheduler iptal edildi")

    async def stop(self) -> None:
        """Döngüyü durdurur."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        await logger.ainfo("Maintenance scheduler durduruldu")

    async def run_once(self) -> None:
        """Tek tick — due schedule'ları işle + overdue'leri missed yap.

        Dışarıdan (test vb.) manuel olarak da çağrılabilir.
        """
        now = datetime.now(UTC)
        due_schedules = await self._db.list_due_maintenance_schedules(now)
        for sched in due_schedules:
            await self._process_due_schedule(sched, now)
        await self._mark_overdue_as_missed(now)

    async def _process_due_schedule(
        self,
        sched: MaintenanceSchedule,
        now: datetime,
    ) -> None:
        """Schedule için task'(lar) açıp next_due_at'i ilerletir."""
        if sched.id is None:
            return
        checklist = await self._db.get_maintenance_checklist(sched.checklist_id)
        if checklist is None:
            await logger.awarning(
                "Schedule checklist'i bulunamadı",
                schedule_id=sched.id,
                checklist_id=sched.checklist_id,
            )
            return

        # Hedef instance listesi: ya tek instance ya template'in tüm
        # instance'ları. Hiçbiri yoksa (template boş) yine tek bir
        # asset_instance_id=None task üretiriz — operator kaydı görür.
        instance_ids: list[int | None]
        if sched.asset_instance_id is not None:
            instance_ids = [sched.asset_instance_id]
        elif sched.asset_template_id is not None:
            instances = await self._db.list_asset_instances(
                template_id=sched.asset_template_id,
            )
            instance_ids = [i.id for i in instances if i.id is not None]
            if not instance_ids:
                instance_ids = [None]
        else:
            instance_ids = [None]

        for iid in instance_ids:
            task = MaintenanceTask(
                schedule_id=sched.id,
                checklist_id=checklist.id or 0,
                asset_instance_id=iid,
                source="schedule",
                title_snapshot=checklist.title,
                due_at=sched.next_due_at,
                status="pending",
            )
            await self._db.insert_maintenance_task(task)

        # next_due_at'i ilerlet
        new_due = compute_next_due_at(
            sched.next_due_at,
            sched.period_kind,
            sched.period_value,
        )
        await self._db.update_maintenance_schedule(
            sched.id,
            {"next_due_at": new_due},
        )
        await logger.ainfo(
            "Bakım task(lar)ı oluşturuldu",
            schedule_id=sched.id,
            instance_count=len(instance_ids),
            next_due_at=new_due.isoformat(),
        )

    async def _mark_overdue_as_missed(self, now: datetime) -> None:
        """due_at + MISSED_THRESHOLD_HOURS'u geçmiş pending task'ları 'missed' işaretle."""
        threshold = now - timedelta(hours=MISSED_THRESHOLD_HOURS)
        # list_upcoming 'due_at <= now + X' arar; 0 saat içinde zaten vadesi
        # gelmiş ve geçmişleri döndürür. Filtreyi Python tarafında uyguluyoruz.
        tasks = await self._db.list_upcoming_maintenance_tasks(within_hours=0)
        for task in tasks:
            if (
                task.due_at is not None
                and task.due_at < threshold
                and task.status == "pending"
                and task.id is not None
            ):
                await self._db.update_maintenance_task(
                    task.id,
                    {"status": "missed"},
                )
