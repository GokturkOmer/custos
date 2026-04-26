"""KPI hesaplama motoru.

Analytics sürecinde periyodik olarak çalışan arka plan task'ı.
Aktif asset instance'ların bağlı tag'lerinin son değerlerini alır,
KPI formüllerini güvenli şekilde değerlendirir, sonuçları kaydeder.
"""

from __future__ import annotations

import ast
import asyncio
from datetime import UTC, datetime

import structlog

from custos.shared.database import (
    AuditLogEntry,
    DatabaseInterface,
    KpiResult,
)

logger = structlog.get_logger(logger_name="kpi_engine")

# Güvenli formül değerlendirmede izin verilen AST node tipleri
_ALLOWED_NODE_TYPES: frozenset[type] = frozenset(
    {
        ast.Expression,
        ast.BinOp,
        ast.UnaryOp,
        ast.Constant,
        ast.Name,
        ast.Load,
        # Operatörler
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.USub,
        ast.UAdd,
    }
)


def _safe_eval(formula: str, variables: dict[str, float]) -> float | None:
    """KPI formülünü güvenli şekilde değerlendirir.

    Sadece aritmetik operatörler (+, -, *, /) ve parantez izinlidir.
    Fonksiyon çağrısı, import, attribute erişimi yasaktır.
    Sıfıra bölme durumunda None döndürür.
    """
    try:
        tree = ast.parse(formula, mode="eval")
    except SyntaxError:
        return None

    # AST ağacındaki tüm node'ları kontrol et
    for node in ast.walk(tree):
        if type(node) not in _ALLOWED_NODE_TYPES:
            return None

    # Name node'larını değişken dict'inden çöz
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id not in variables:
            return None

    # Değerlendirme — sıfıra bölme korumalı
    try:
        result = eval(  # noqa: S307 — AST doğrulamasıyla güvenli
            compile(tree, "<kpi>", "eval"),
            {"__builtins__": {}},
            variables,
        )
    except (ZeroDivisionError, TypeError, OverflowError):
        return None

    if not isinstance(result, int | float):
        return None

    return float(result)


class KpiEngine:
    """Asset instance'lar için KPI hesaplama motoru.

    Periyodik olarak aktif asset instance'ların bağlı tag'lerinin
    son değerlerini alır, KPI formüllerini değerlendirir,
    sonuçları kaydeder.
    """

    def __init__(
        self,
        db: DatabaseInterface,
        interval_seconds: float = 60.0,
    ) -> None:
        self._db = db
        self._interval = interval_seconds
        self._task: asyncio.Task[None] | None = None
        self._running = False

    async def start(self) -> None:
        """Engine'i başlatır — arka plan task olarak çalışır."""
        self._running = True
        await logger.ainfo(
            "KPI engine başlatıldı",
            interval=self._interval,
        )
        try:
            while self._running:
                try:
                    await self._compute_cycle()
                except Exception:
                    await logger.aerror(
                        "KPI hesaplama döngüsünde hata",
                        exc_info=True,
                    )
                await asyncio.sleep(self._interval)
        except asyncio.CancelledError:
            await logger.ainfo("KPI engine iptal edildi")

    async def stop(self) -> None:
        """Engine'i durdurur."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        await logger.ainfo("KPI engine durduruldu")

    async def _compute_cycle(self) -> None:
        """Tek bir hesaplama döngüsü — tüm aktif instance'lar için KPI hesaplar."""
        instances = await self._db.list_asset_instances(status="active")
        if not instances:
            return

        now = datetime.now(UTC)
        # Bucket başlangıcı: dakikanın başı (saniye ve mikrosaniye sıfırlanır)
        bucket_start = now.replace(second=0, microsecond=0)

        total_computed = 0
        results_batch: list[KpiResult] = []

        for instance in instances:
            assert instance.id is not None
            tmpl = await self._db.get_asset_template(instance.template_id)
            if tmpl is None or not tmpl.kpi_definitions:
                continue

            # Tag binding'leri çek
            bindings = await self._db.list_tag_bindings(instance.id)
            if not bindings:
                continue

            # role_id → role_key haritası
            role_map: dict[int, str] = {}
            for role in tmpl.roles:
                if role.id is not None:
                    role_map[role.id] = role.role_key

            # Bağlı tag'lerin son değerlerini çek
            tag_ids = [b.tag_id for b in bindings]
            readings = await self._db.get_latest_tag_readings(tag_ids)

            # role_key → değer haritası
            variables: dict[str, float] = {}
            for binding in bindings:
                role_key = role_map.get(binding.role_id)
                reading = readings.get(binding.tag_id)
                if role_key is not None and reading is not None:
                    variables[role_key] = reading.value

            # Her KPI formülünü değerlendir
            for kpi_def in tmpl.kpi_definitions:
                assert kpi_def.id is not None
                value = _safe_eval(kpi_def.formula, variables)
                if value is not None:
                    results_batch.append(
                        KpiResult(
                            instance_id=instance.id,
                            kpi_definition_id=kpi_def.id,
                            bucket_start=bucket_start,
                            value=value,
                        )
                    )
                    total_computed += 1

        # Toplu kaydet
        if results_batch:
            await self._db.insert_kpi_results_batch(results_batch)

        if total_computed > 0:
            await logger.ainfo(
                "KPI hesaplama tamamlandı",
                computed=total_computed,
                instances=len(instances),
            )
            await self._db.insert_audit_log(
                AuditLogEntry(
                    category="kpi",
                    action="compute_cycle",
                    detail=f"{total_computed} KPI hesaplandı",
                )
            )
