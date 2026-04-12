"""Veritabanı abstract arayüzü ve TimescaleDB implementasyonu.

Mimari prensip: tüm veritabanı erişimi bu modüldeki abstract
arayüz üzerinden yapılır. Modüllerden doğrudan SQL/ORM çağrısı
yapılmaz.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import UTC, datetime, time

import asyncpg
import structlog

from custos.shared.config import Settings

logger = structlog.get_logger(logger_name="database")


@dataclass(frozen=True)
class TagReading:
    """Tek bir tag okuması.

    Collector'dan veritabanına aktarılan temel veri birimi.
    """

    timestamp: datetime
    tag_id: str
    value: float
    quality_flag: int = 0


@dataclass
class ConnectionProfile:
    """Connection profile kaydı — connection_profiles tablosunun Python temsili."""

    name: str
    host: str
    port: int = 502
    unit_id_start: int = 1
    unit_id_end: int = 1
    status: str = "idle"
    last_scan_at: datetime | None = None
    slave_latency_min_ms: float | None = None
    slave_latency_avg_ms: float | None = None
    slave_latency_max_ms: float | None = None
    id: int | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class TagRecord:
    """Tag tanım kaydı — tags tablosunun Python temsili."""

    tag_id: str
    name: str
    modbus_host: str
    register_address: int
    modbus_port: int = 502
    unit_id: int = 1
    register_type: str = "uint16"
    byte_order: str = "big"
    gain: float = 1.0
    offset: float = 0.0
    unit: str = ""
    polling_interval_ms: int = 10000
    polling_preset: str = "slow"
    status: str = "active"
    id: int | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class AssetTemplate:
    """Asset template kaydı — endüstriyel ekipman tipi tanımı."""

    slug: str
    name: str
    description: str = ""
    icon: str = "cpu"
    id: int | None = None
    created_at: datetime | None = None
    roles: list[TemplateRole] = field(default_factory=list)
    kpi_definitions: list[KpiDefinition] = field(default_factory=list)


@dataclass
class TemplateRole:
    """Template role kaydı — bir template'in beklediği tag yuvası."""

    template_id: int
    role_key: str
    label: str
    unit_hint: str = ""
    required: bool = True
    sort_order: int = 0
    id: int | None = None


@dataclass
class KpiDefinition:
    """KPI tanımı — template bazında hesaplanacak formül."""

    template_id: int
    name: str
    formula: str
    unit: str = ""
    description: str = ""
    id: int | None = None


@dataclass
class AssetInstance:
    """Asset instance kaydı — bir template'in somut kurulumu."""

    template_id: int
    name: str
    description: str = ""
    location: str = ""
    status: str = "active"
    id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class TagBinding:
    """Tag binding kaydı — instance role'üne bağlı tag."""

    instance_id: int
    role_id: int
    tag_id: str
    id: int | None = None
    created_at: datetime | None = None


@dataclass
class Threshold:
    """Alarm eşik tanımı — ISA-18.2 uyumlu threshold kaydı."""

    tag_id: str
    name: str
    direction: str = "high"  # 'high' / 'low'
    set_point: float = 0.0
    severity: str = "warn"  # 'warn' / 'crit'
    debounce_seconds: int = 5
    hysteresis: float = 0.0
    enabled: bool = True
    id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class AlarmEvent:
    """Alarm event kaydı — ISA-18.2 state machine durumu."""

    threshold_id: int
    tag_id: str
    state: str = "triggered"  # 'triggered' / 'acknowledged' / 'cleared'
    triggered_at: datetime | None = None
    acknowledged_at: datetime | None = None
    cleared_at: datetime | None = None
    trigger_value: float = 0.0
    clear_value: float | None = None
    notes: str = ""
    id: int | None = None
    created_at: datetime | None = None


@dataclass
class AuditLogEntry:
    """Audit log kaydı — sistem olaylarının kronolojik kaydı."""

    category: str
    action: str
    entity_type: str = ""
    entity_id: str = ""
    detail: str = ""
    id: int | None = None
    timestamp: datetime | None = None


@dataclass
class KpiResult:
    """KPI hesaplama sonucu — kpi_results tablosunun Python temsili."""

    instance_id: int
    kpi_definition_id: int
    bucket_start: datetime
    value: float
    id: int | None = None
    created_at: datetime | None = None


@dataclass
class AnomalyScore:
    """Anomali skoru — anomaly_scores tablosunun Python temsili."""

    instance_id: int
    timestamp: datetime
    score: float
    is_anomaly: bool = False
    feature_vector: str = ""
    id: int | None = None
    created_at: datetime | None = None


@dataclass
class PushSubscription:
    """Web Push bildirim aboneliği — push_subscriptions tablosunun Python temsili."""

    endpoint: str
    p256dh: str
    auth: str
    notify_warn: bool = True
    notify_crit: bool = True
    quiet_start: time | None = None
    quiet_end: time | None = None
    id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class DatabaseInterface(abc.ABC):
    """Veritabanı erişim arayüzü.

    Tüm modüller bu arayüz üzerinden veritabanına erişir.
    Concrete implementasyonlar (TimescaleDB, InMemory vb.)
    bu sınıfı miras alır.
    """

    @abc.abstractmethod
    async def connect(self) -> None:
        """Veritabanına bağlantı havuzu oluşturur."""

    @abc.abstractmethod
    async def close(self) -> None:
        """Bağlantı havuzunu kapatır."""

    @abc.abstractmethod
    async def health_check(self) -> bool:
        """Veritabanının erişilebilir olup olmadığını kontrol eder."""

    # --- Tag Reading CRUD ---

    @abc.abstractmethod
    async def insert_tag_reading(
        self,
        timestamp: datetime,
        tag_id: str,
        value: float,
        quality_flag: int,
    ) -> None:
        """Tag okumasını kaydeder."""

    @abc.abstractmethod
    async def insert_tag_readings_batch(
        self,
        readings: list[TagReading],
    ) -> None:
        """Çoklu tag okumasını tek batch halinde veritabanına yazar."""

    @abc.abstractmethod
    async def query_tag_readings(
        self,
        tag_id: str,
        start: datetime,
        end: datetime,
    ) -> list[TagReading]:
        """Belirli bir tag'in zaman aralığındaki okumalarını sorgular."""

    # --- Tag CRUD ---

    @abc.abstractmethod
    async def insert_tag(self, tag: TagRecord) -> TagRecord:
        """Yeni tag kaydı oluşturur."""

    @abc.abstractmethod
    async def update_tag(self, tag_id: str, updates: dict[str, object]) -> TagRecord | None:
        """Tag kaydını günceller. Bulunamazsa None döndürür."""

    @abc.abstractmethod
    async def delete_tag(self, tag_id: str) -> bool:
        """Tag kaydını siler. Başarılıysa True döndürür."""

    @abc.abstractmethod
    async def get_tag(self, tag_id: str) -> TagRecord | None:
        """Tek bir tag kaydını getirir. Bulunamazsa None döndürür."""

    @abc.abstractmethod
    async def list_tags(self, status: str | None = None) -> list[TagRecord]:
        """Tag listesini döndürür. Opsiyonel status filtresi."""

    # --- Connection Profile CRUD ---

    @abc.abstractmethod
    async def insert_connection_profile(
        self,
        profile: ConnectionProfile,
    ) -> ConnectionProfile:
        """Yeni connection profile kaydı oluşturur."""

    @abc.abstractmethod
    async def update_connection_profile(
        self,
        profile_id: int,
        updates: dict[str, object],
    ) -> ConnectionProfile | None:
        """Connection profile kaydını günceller. Bulunamazsa None döndürür."""

    @abc.abstractmethod
    async def delete_connection_profile(self, profile_id: int) -> bool:
        """Connection profile kaydını siler. Başarılıysa True döndürür."""

    @abc.abstractmethod
    async def get_connection_profile(self, profile_id: int) -> ConnectionProfile | None:
        """Tek bir connection profile kaydını getirir. Bulunamazsa None döndürür."""

    @abc.abstractmethod
    async def list_connection_profiles(self) -> list[ConnectionProfile]:
        """Tüm connection profile'ları döndürür."""

    # --- Live Readings ---

    @abc.abstractmethod
    async def get_latest_tag_readings(
        self,
        tag_ids: list[str],
    ) -> dict[str, TagReading]:
        """Her tag için en son okumayı döndürür."""

    # --- Feature & Label (stub) ---

    @abc.abstractmethod
    async def insert_feature(
        self,
        timestamp: datetime,
        tag_id: str,
        feature_name: str,
        feature_value: float,
        window_size_seconds: int,
    ) -> None:
        """Hesaplanmış bir özelliği kaydeder."""

    @abc.abstractmethod
    async def insert_label(
        self,
        timestamp_start: datetime,
        timestamp_end: datetime,
        event_type: str,
        confidence: str,
        source: str,
        notes: str | None,
    ) -> None:
        """Etiket kaydı oluşturur."""

    # --- Asset Template (read-only) ---

    @abc.abstractmethod
    async def list_asset_templates(self) -> list[AssetTemplate]:
        """Template'leri roles ve kpi_definitions ile birlikte döndürür."""

    @abc.abstractmethod
    async def get_asset_template(self, template_id: int) -> AssetTemplate | None:
        """Tekil template (roles + kpi dahil). Bulunamazsa None döndürür."""

    # --- Asset Instance CRUD ---

    @abc.abstractmethod
    async def insert_asset_instance(self, instance: AssetInstance) -> AssetInstance:
        """Yeni asset instance kaydı oluşturur."""

    @abc.abstractmethod
    async def update_asset_instance(
        self,
        instance_id: int,
        updates: dict[str, object],
    ) -> AssetInstance | None:
        """Asset instance kaydını günceller. Bulunamazsa None döndürür."""

    @abc.abstractmethod
    async def delete_asset_instance(self, instance_id: int) -> bool:
        """Asset instance kaydını siler. Başarılıysa True döndürür."""

    @abc.abstractmethod
    async def get_asset_instance(self, instance_id: int) -> AssetInstance | None:
        """Tek bir asset instance kaydını getirir. Bulunamazsa None döndürür."""

    @abc.abstractmethod
    async def list_asset_instances(
        self,
        template_id: int | None = None,
        status: str | None = None,
    ) -> list[AssetInstance]:
        """Asset instance listesini döndürür. Opsiyonel filtreler."""

    # --- Tag Binding CRUD ---

    @abc.abstractmethod
    async def insert_tag_binding(self, binding: TagBinding) -> TagBinding:
        """Yeni tag binding kaydı oluşturur."""

    @abc.abstractmethod
    async def delete_tag_binding(self, binding_id: int) -> bool:
        """Tag binding kaydını siler. Başarılıysa True döndürür."""

    @abc.abstractmethod
    async def list_tag_bindings(self, instance_id: int) -> list[TagBinding]:
        """Bir instance'ın tüm tag binding'lerini döndürür."""

    @abc.abstractmethod
    async def replace_tag_bindings(
        self,
        instance_id: int,
        bindings: list[TagBinding],
    ) -> list[TagBinding]:
        """Mevcut binding'leri silip yenileriyle değiştirir."""

    # --- Threshold CRUD ---

    @abc.abstractmethod
    async def insert_threshold(self, threshold: Threshold) -> Threshold:
        """Yeni threshold kaydı oluşturur."""

    @abc.abstractmethod
    async def update_threshold(
        self,
        threshold_id: int,
        updates: dict[str, object],
    ) -> Threshold | None:
        """Threshold kaydını günceller. Bulunamazsa None döndürür."""

    @abc.abstractmethod
    async def delete_threshold(self, threshold_id: int) -> bool:
        """Threshold kaydını siler. Başarılıysa True döndürür."""

    @abc.abstractmethod
    async def get_threshold(self, threshold_id: int) -> Threshold | None:
        """Tek bir threshold kaydını getirir. Bulunamazsa None döndürür."""

    @abc.abstractmethod
    async def list_thresholds(
        self,
        tag_id: str | None = None,
        enabled: bool | None = None,
    ) -> list[Threshold]:
        """Threshold listesini döndürür. Opsiyonel filtreler."""

    # --- Alarm Event CRUD ---

    @abc.abstractmethod
    async def insert_alarm_event(self, event: AlarmEvent) -> AlarmEvent:
        """Yeni alarm event kaydı oluşturur."""

    @abc.abstractmethod
    async def update_alarm_event(
        self,
        event_id: int,
        updates: dict[str, object],
    ) -> AlarmEvent | None:
        """Alarm event kaydını günceller. Bulunamazsa None döndürür."""

    @abc.abstractmethod
    async def get_alarm_event(self, event_id: int) -> AlarmEvent | None:
        """Tek bir alarm event kaydını getirir. Bulunamazsa None döndürür."""

    @abc.abstractmethod
    async def list_alarm_events(
        self,
        state: str | None = None,
        tag_id: str | None = None,
        limit: int = 100,
    ) -> list[AlarmEvent]:
        """Alarm event listesini döndürür. Opsiyonel filtreler."""

    @abc.abstractmethod
    async def get_active_alarm_for_threshold(
        self,
        threshold_id: int,
    ) -> AlarmEvent | None:
        """Threshold için aktif (cleared olmayan) alarm döndürür."""

    # --- Audit Log ---

    @abc.abstractmethod
    async def insert_audit_log(self, entry: AuditLogEntry) -> AuditLogEntry:
        """Yeni audit log kaydı oluşturur."""

    @abc.abstractmethod
    async def list_audit_log(
        self,
        category: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditLogEntry]:
        """Audit log listesini döndürür. Opsiyonel filtreler."""

    @abc.abstractmethod
    async def count_audit_log(self, category: str | None = None) -> int:
        """Audit log kayıt sayısını döndürür."""

    # --- KPI Results ---

    @abc.abstractmethod
    async def insert_kpi_result(self, result: KpiResult) -> KpiResult:
        """Yeni KPI sonucu kaydeder."""

    @abc.abstractmethod
    async def insert_kpi_results_batch(self, results: list[KpiResult]) -> None:
        """Çoklu KPI sonucunu tek batch halinde yazar."""

    @abc.abstractmethod
    async def list_kpi_results(
        self,
        instance_id: int,
        kpi_definition_id: int | None = None,
        limit: int = 100,
    ) -> list[KpiResult]:
        """KPI sonuç listesini döndürür."""

    @abc.abstractmethod
    async def get_latest_kpi_results(
        self,
        instance_id: int,
    ) -> dict[int, KpiResult]:
        """Her KPI definition için en son hesaplanan değeri döndürür."""

    # --- Anomaly Scores ---

    @abc.abstractmethod
    async def insert_anomaly_score(self, score: AnomalyScore) -> AnomalyScore:
        """Yeni anomali skoru kaydeder."""

    @abc.abstractmethod
    async def list_anomaly_scores(
        self,
        instance_id: int,
        limit: int = 100,
    ) -> list[AnomalyScore]:
        """Anomali skor listesini döndürür."""

    @abc.abstractmethod
    async def get_latest_anomaly_score(
        self,
        instance_id: int,
    ) -> AnomalyScore | None:
        """En son anomali skorunu döndürür."""

    @abc.abstractmethod
    async def count_anomalies(
        self,
        since: datetime | None = None,
    ) -> int:
        """Anomali sayısını döndürür. Opsiyonel zaman filtresi."""

    # --- Push Subscriptions ---

    @abc.abstractmethod
    async def upsert_push_subscription(
        self,
        sub: PushSubscription,
    ) -> PushSubscription:
        """Push subscription kaydeder veya günceller (endpoint bazlı upsert)."""

    @abc.abstractmethod
    async def delete_push_subscription(self, endpoint: str) -> bool:
        """Push subscription siler. Başarılıysa True döndürür."""

    @abc.abstractmethod
    async def list_push_subscriptions(self) -> list[PushSubscription]:
        """Tüm push subscription'ları döndürür."""

    @abc.abstractmethod
    async def update_push_subscription_settings(
        self,
        endpoint: str,
        updates: dict[str, object],
    ) -> PushSubscription | None:
        """Push subscription ayarlarını günceller. Bulunamazsa None döndürür."""


# İzin verilen güncelleme alanları — Connection Profile (SQL injection önlemi)
_ALLOWED_PROFILE_UPDATE_FIELDS: frozenset[str] = frozenset({
    "name", "host", "port", "unit_id_start", "unit_id_end",
    "status", "last_scan_at",
    "slave_latency_min_ms", "slave_latency_avg_ms", "slave_latency_max_ms",
})


def _row_to_connection_profile(row: asyncpg.Record) -> ConnectionProfile:
    """asyncpg satırını ConnectionProfile'a dönüştürür."""
    return ConnectionProfile(
        id=row["id"],
        name=row["name"],
        host=row["host"],
        port=row["port"],
        unit_id_start=row["unit_id_start"],
        unit_id_end=row["unit_id_end"],
        status=row["status"],
        last_scan_at=row["last_scan_at"],
        slave_latency_min_ms=(
            float(row["slave_latency_min_ms"])
            if row["slave_latency_min_ms"] is not None
            else None
        ),
        slave_latency_avg_ms=(
            float(row["slave_latency_avg_ms"])
            if row["slave_latency_avg_ms"] is not None
            else None
        ),
        slave_latency_max_ms=(
            float(row["slave_latency_max_ms"])
            if row["slave_latency_max_ms"] is not None
            else None
        ),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


# İzin verilen güncelleme alanları — Tag (SQL injection önlemi)
_ALLOWED_TAG_UPDATE_FIELDS: frozenset[str] = frozenset({
    "name", "modbus_host", "modbus_port", "unit_id",
    "register_address", "register_type", "byte_order",
    "gain", "offset", "unit", "polling_interval_ms",
    "polling_preset", "status",
})


def _row_to_tag_record(row: asyncpg.Record) -> TagRecord:
    """asyncpg satırını TagRecord'a dönüştürür."""
    return TagRecord(
        id=row["id"],
        tag_id=row["tag_id"],
        name=row["name"],
        modbus_host=row["modbus_host"],
        modbus_port=row["modbus_port"],
        unit_id=row["unit_id"],
        register_address=row["register_address"],
        register_type=row["register_type"],
        byte_order=row["byte_order"],
        gain=float(row["gain"]),
        offset=float(row["offset"]),
        unit=row["unit"],
        polling_interval_ms=row["polling_interval_ms"],
        polling_preset=row["polling_preset"],
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


# İzin verilen güncelleme alanları — Asset Instance (SQL injection önlemi)
_ALLOWED_INSTANCE_UPDATE_FIELDS: frozenset[str] = frozenset({
    "name", "description", "location", "status",
})


def _row_to_template_role(row: asyncpg.Record) -> TemplateRole:
    """asyncpg satırını TemplateRole'e dönüştürür."""
    return TemplateRole(
        id=row["id"],
        template_id=row["template_id"],
        role_key=row["role_key"],
        label=row["label"],
        unit_hint=row["unit_hint"],
        required=row["required"],
        sort_order=row["sort_order"],
    )


def _row_to_kpi_definition(row: asyncpg.Record) -> KpiDefinition:
    """asyncpg satırını KpiDefinition'a dönüştürür."""
    return KpiDefinition(
        id=row["id"],
        template_id=row["template_id"],
        name=row["name"],
        formula=row["formula"],
        unit=row["unit"],
        description=row["description"],
    )


def _row_to_asset_template(row: asyncpg.Record) -> AssetTemplate:
    """asyncpg satırını AssetTemplate'e dönüştürür (roles/kpi boş)."""
    return AssetTemplate(
        id=row["id"],
        slug=row["slug"],
        name=row["name"],
        description=row["description"],
        icon=row["icon"],
        created_at=row["created_at"],
    )


def _row_to_asset_instance(row: asyncpg.Record) -> AssetInstance:
    """asyncpg satırını AssetInstance'a dönüştürür."""
    return AssetInstance(
        id=row["id"],
        template_id=row["template_id"],
        name=row["name"],
        description=row["description"],
        location=row["location"],
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_tag_binding(row: asyncpg.Record) -> TagBinding:
    """asyncpg satırını TagBinding'e dönüştürür."""
    return TagBinding(
        id=row["id"],
        instance_id=row["instance_id"],
        role_id=row["role_id"],
        tag_id=row["tag_id"],
        created_at=row["created_at"],
    )


# İzin verilen güncelleme alanları — Threshold (SQL injection önlemi)
_ALLOWED_THRESHOLD_UPDATE_FIELDS: frozenset[str] = frozenset({
    "name", "direction", "set_point", "severity",
    "debounce_seconds", "hysteresis", "enabled",
})

# İzin verilen güncelleme alanları — Alarm Event (SQL injection önlemi)
_ALLOWED_ALARM_EVENT_UPDATE_FIELDS: frozenset[str] = frozenset({
    "state", "acknowledged_at", "cleared_at", "clear_value", "notes",
})


def _row_to_threshold(row: asyncpg.Record) -> Threshold:
    """asyncpg satırını Threshold'a dönüştürür."""
    return Threshold(
        id=row["id"],
        tag_id=row["tag_id"],
        name=row["name"],
        direction=row["direction"],
        set_point=float(row["set_point"]),
        severity=row["severity"],
        debounce_seconds=row["debounce_seconds"],
        hysteresis=float(row["hysteresis"]),
        enabled=row["enabled"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_alarm_event(row: asyncpg.Record) -> AlarmEvent:
    """asyncpg satırını AlarmEvent'e dönüştürür."""
    return AlarmEvent(
        id=row["id"],
        threshold_id=row["threshold_id"],
        tag_id=row["tag_id"],
        state=row["state"],
        triggered_at=row["triggered_at"],
        acknowledged_at=row["acknowledged_at"],
        cleared_at=row["cleared_at"],
        trigger_value=float(row["trigger_value"]),
        clear_value=float(row["clear_value"]) if row["clear_value"] is not None else None,
        notes=row["notes"],
        created_at=row["created_at"],
    )


def _row_to_audit_log_entry(row: asyncpg.Record) -> AuditLogEntry:
    """asyncpg satırını AuditLogEntry'ye dönüştürür."""
    return AuditLogEntry(
        id=row["id"],
        timestamp=row["timestamp"],
        category=row["category"],
        action=row["action"],
        entity_type=row["entity_type"],
        entity_id=row["entity_id"],
        detail=row["detail"],
    )


def _row_to_kpi_result(row: asyncpg.Record) -> KpiResult:
    """asyncpg satırını KpiResult'a dönüştürür."""
    return KpiResult(
        id=row["id"],
        instance_id=row["instance_id"],
        kpi_definition_id=row["kpi_definition_id"],
        bucket_start=row["bucket_start"],
        value=row["value"],
        created_at=row["created_at"],
    )


def _row_to_anomaly_score(row: asyncpg.Record) -> AnomalyScore:
    """asyncpg satırını AnomalyScore'a dönüştürür."""
    return AnomalyScore(
        id=row["id"],
        instance_id=row["instance_id"],
        timestamp=row["timestamp"],
        score=row["score"],
        is_anomaly=row["is_anomaly"],
        feature_vector=row["feature_vector"],
        created_at=row["created_at"],
    )


# İzin verilen güncelleme alanları — Push Subscription (SQL injection önlemi)
_ALLOWED_PUSH_SUB_UPDATE_FIELDS: frozenset[str] = frozenset({
    "notify_warn", "notify_crit", "quiet_start", "quiet_end",
})


def _row_to_push_subscription(row: asyncpg.Record) -> PushSubscription:
    """asyncpg satırını PushSubscription'a dönüştürür."""
    return PushSubscription(
        id=row["id"],
        endpoint=row["endpoint"],
        p256dh=row["p256dh"],
        auth=row["auth"],
        notify_warn=row["notify_warn"],
        notify_crit=row["notify_crit"],
        quiet_start=row["quiet_start"],
        quiet_end=row["quiet_end"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class TimescaleDBDatabase(DatabaseInterface):
    """TimescaleDB (PostgreSQL) implementasyonu.

    asyncpg bağlantı havuzu kullanarak asenkron veritabanı
    erişimi sağlar.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._pool: asyncpg.Pool[asyncpg.Record] | None = None

    def _get_pool(self) -> asyncpg.Pool[asyncpg.Record]:
        """Bağlantı havuzunu döndürür, yoksa hata fırlatır."""
        if self._pool is None:
            msg = "Veritabanı bağlantı havuzu oluşturulmamış. connect() çağrıldı mı?"
            raise RuntimeError(msg)
        return self._pool

    async def connect(self) -> None:
        """asyncpg bağlantı havuzu oluşturur."""
        self._pool = await asyncpg.create_pool(
            dsn=self._settings.database_url_async,
            min_size=2,
            max_size=10,
            server_settings={"client_encoding": "UTF8"},
        )
        await logger.ainfo("Veritabanı bağlantı havuzu oluşturuldu")

    async def close(self) -> None:
        """Bağlantı havuzunu kapatır."""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
            await logger.ainfo("Veritabanı bağlantı havuzu kapatıldı")

    async def health_check(self) -> bool:
        """SELECT 1 ile veritabanı erişilebilirliğini kontrol eder."""
        if self._pool is None:
            await logger.awarning("Sağlık kontrolü: bağlantı havuzu yok")
            return False
        try:
            async with self._pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            return True
        except Exception:
            await logger.aerror("Sağlık kontrolü başarısız", exc_info=True)
            return False

    # --- Tag Reading implementasyonları ---

    async def insert_tag_reading(
        self,
        timestamp: datetime,
        tag_id: str,
        value: float,
        quality_flag: int,
    ) -> None:
        """Tag okumasını kaydeder (batch'e delege eder)."""
        reading = TagReading(
            timestamp=timestamp,
            tag_id=tag_id,
            value=value,
            quality_flag=quality_flag,
        )
        await self.insert_tag_readings_batch([reading])

    async def insert_tag_readings_batch(
        self,
        readings: list[TagReading],
    ) -> None:
        """Çoklu tag okumasını tek batch halinde veritabanına yazar."""
        pool = self._get_pool()
        args = [(r.timestamp, r.tag_id, r.value, r.quality_flag) for r in readings]
        async with pool.acquire() as conn:
            await conn.executemany(
                "INSERT INTO tag_readings (timestamp, tag_id, value, quality_flag) "
                "VALUES ($1, $2, $3, $4)",
                args,
            )

    async def query_tag_readings(
        self,
        tag_id: str,
        start: datetime,
        end: datetime,
    ) -> list[TagReading]:
        """Belirli bir tag'in zaman aralığındaki okumalarını sorgular."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT timestamp, tag_id, value, quality_flag "
                "FROM tag_readings "
                "WHERE tag_id = $1 AND timestamp >= $2 AND timestamp <= $3 "
                "ORDER BY timestamp ASC",
                tag_id,
                start,
                end,
            )
        return [
            TagReading(
                timestamp=row["timestamp"],
                tag_id=row["tag_id"],
                value=float(row["value"]),
                quality_flag=int(row["quality_flag"]),
            )
            for row in rows
        ]

    # --- Tag CRUD implementasyonları ---

    async def insert_tag(self, tag: TagRecord) -> TagRecord:
        """Yeni tag kaydı oluşturur ve döndürür."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                'INSERT INTO tags '
                '(tag_id, name, modbus_host, modbus_port, unit_id, '
                'register_address, register_type, byte_order, '
                'gain, "offset", unit, polling_interval_ms, polling_preset, status) '
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14) "
                "RETURNING *",
                tag.tag_id, tag.name, tag.modbus_host, tag.modbus_port,
                tag.unit_id, tag.register_address, tag.register_type,
                tag.byte_order, tag.gain, tag.offset, tag.unit,
                tag.polling_interval_ms, tag.polling_preset, tag.status,
            )
        assert row is not None  # INSERT RETURNING her zaman satır döndürür
        return _row_to_tag_record(row)

    async def update_tag(self, tag_id: str, updates: dict[str, object]) -> TagRecord | None:
        """Tag kaydını günceller. Bilinmeyen alan varsa hata fırlatır."""
        invalid = set(updates.keys()) - _ALLOWED_TAG_UPDATE_FIELDS
        if invalid:
            msg = f"Güncellenemeyen alanlar: {invalid}"
            raise ValueError(msg)

        if not updates:
            return await self.get_tag(tag_id)

        # Dinamik SET cümlesi oluştur (alan adları whitelist'ten geldiği için güvenli)
        set_parts: list[str] = []
        values: list[object] = []
        for i, (col, val) in enumerate(updates.items(), start=1):
            # "offset" PostgreSQL reserved word olduğu için tırnak içine al
            col_name = f'"{col}"' if col == "offset" else col
            set_parts.append(f"{col_name} = ${i}")
            values.append(val)

        # updated_at'i de güncelle
        idx = len(values) + 1
        set_parts.append(f"updated_at = ${idx}")
        values.append(datetime.now(UTC))

        # WHERE koşulu
        idx_where = len(values) + 1
        values.append(tag_id)

        sql = f"UPDATE tags SET {', '.join(set_parts)} WHERE tag_id = ${idx_where} RETURNING *"

        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(sql, *values)

        if row is None:
            return None
        return _row_to_tag_record(row)

    async def delete_tag(self, tag_id: str) -> bool:
        """Tag kaydını siler."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM tags WHERE tag_id = $1",
                tag_id,
            )
        return str(result) == "DELETE 1"

    async def get_tag(self, tag_id: str) -> TagRecord | None:
        """Tek bir tag kaydını getirir."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM tags WHERE tag_id = $1",
                tag_id,
            )
        if row is None:
            return None
        return _row_to_tag_record(row)

    async def list_tags(self, status: str | None = None) -> list[TagRecord]:
        """Tag listesini döndürür."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            if status is not None:
                rows = await conn.fetch(
                    "SELECT * FROM tags WHERE status = $1 ORDER BY tag_id",
                    status,
                )
            else:
                rows = await conn.fetch("SELECT * FROM tags ORDER BY tag_id")
        return [_row_to_tag_record(row) for row in rows]

    # --- Connection Profile CRUD implementasyonları ---

    async def insert_connection_profile(
        self,
        profile: ConnectionProfile,
    ) -> ConnectionProfile:
        """Yeni connection profile kaydı oluşturur ve döndürür."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO connection_profiles "
                "(name, host, port, unit_id_start, unit_id_end, status) "
                "VALUES ($1, $2, $3, $4, $5, $6) "
                "RETURNING *",
                profile.name, profile.host, profile.port,
                profile.unit_id_start, profile.unit_id_end, profile.status,
            )
        assert row is not None  # INSERT RETURNING her zaman satır döndürür
        return _row_to_connection_profile(row)

    async def update_connection_profile(
        self,
        profile_id: int,
        updates: dict[str, object],
    ) -> ConnectionProfile | None:
        """Connection profile kaydını günceller."""
        invalid = set(updates.keys()) - _ALLOWED_PROFILE_UPDATE_FIELDS
        if invalid:
            msg = f"Güncellenemeyen alanlar: {invalid}"
            raise ValueError(msg)

        if not updates:
            return await self.get_connection_profile(profile_id)

        # Dinamik SET cümlesi oluştur (alan adları whitelist'ten geldiği için güvenli)
        set_parts: list[str] = []
        values: list[object] = []
        for i, (col, val) in enumerate(updates.items(), start=1):
            set_parts.append(f"{col} = ${i}")
            values.append(val)

        # updated_at'i de güncelle
        idx = len(values) + 1
        set_parts.append(f"updated_at = ${idx}")
        values.append(datetime.now(UTC))

        # WHERE koşulu
        idx_where = len(values) + 1
        values.append(profile_id)

        sql = (
            f"UPDATE connection_profiles SET {', '.join(set_parts)} "
            f"WHERE id = ${idx_where} RETURNING *"
        )

        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(sql, *values)

        if row is None:
            return None
        return _row_to_connection_profile(row)

    async def delete_connection_profile(self, profile_id: int) -> bool:
        """Connection profile kaydını siler."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM connection_profiles WHERE id = $1",
                profile_id,
            )
        return str(result) == "DELETE 1"

    async def get_connection_profile(self, profile_id: int) -> ConnectionProfile | None:
        """Tek bir connection profile kaydını getirir."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM connection_profiles WHERE id = $1",
                profile_id,
            )
        if row is None:
            return None
        return _row_to_connection_profile(row)

    async def list_connection_profiles(self) -> list[ConnectionProfile]:
        """Tüm connection profile'ları döndürür."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM connection_profiles ORDER BY name",
            )
        return [_row_to_connection_profile(row) for row in rows]

    # --- Live Readings implementasyonu ---

    async def get_latest_tag_readings(
        self,
        tag_ids: list[str],
    ) -> dict[str, TagReading]:
        """Her tag için en son okumayı döndürür."""
        if not tag_ids:
            return {}

        pool = self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT DISTINCT ON (tag_id) "
                "timestamp, tag_id, value, quality_flag "
                "FROM tag_readings "
                "WHERE tag_id = ANY($1) "
                "ORDER BY tag_id, timestamp DESC",
                tag_ids,
            )

        return {
            row["tag_id"]: TagReading(
                timestamp=row["timestamp"],
                tag_id=row["tag_id"],
                value=float(row["value"]),
                quality_flag=int(row["quality_flag"]),
            )
            for row in rows
        }

    # --- Feature & Label (stub) ---

    async def insert_feature(
        self,
        timestamp: datetime,
        tag_id: str,
        feature_name: str,
        feature_value: float,
        window_size_seconds: int,
    ) -> None:
        """Hesaplanmış bir özelliği kaydeder."""
        raise NotImplementedError("Aşama 5'te eklenecek")

    async def insert_label(
        self,
        timestamp_start: datetime,
        timestamp_end: datetime,
        event_type: str,
        confidence: str,
        source: str,
        notes: str | None,
    ) -> None:
        """Etiket kaydı oluşturur."""
        raise NotImplementedError("Aşama 5'te eklenecek")

    # --- Asset Template implementasyonları ---

    async def list_asset_templates(self) -> list[AssetTemplate]:
        """Template'leri roles ve kpi_definitions ile birlikte döndürür."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            tmpl_rows = await conn.fetch(
                "SELECT * FROM asset_templates ORDER BY id",
            )
            role_rows = await conn.fetch(
                "SELECT * FROM template_roles ORDER BY template_id, sort_order",
            )
            kpi_rows = await conn.fetch(
                "SELECT * FROM kpi_definitions ORDER BY template_id, id",
            )

        # Role ve KPI'ları template_id bazında grupla
        roles_by_tmpl: dict[int, list[TemplateRole]] = {}
        for row in role_rows:
            tid = row["template_id"]
            roles_by_tmpl.setdefault(tid, []).append(_row_to_template_role(row))

        kpis_by_tmpl: dict[int, list[KpiDefinition]] = {}
        for row in kpi_rows:
            tid = row["template_id"]
            kpis_by_tmpl.setdefault(tid, []).append(_row_to_kpi_definition(row))

        templates: list[AssetTemplate] = []
        for row in tmpl_rows:
            tmpl = _row_to_asset_template(row)
            assert tmpl.id is not None
            tmpl.roles = roles_by_tmpl.get(tmpl.id, [])
            tmpl.kpi_definitions = kpis_by_tmpl.get(tmpl.id, [])
            templates.append(tmpl)

        return templates

    async def get_asset_template(self, template_id: int) -> AssetTemplate | None:
        """Tekil template (roles + kpi dahil)."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            tmpl_row = await conn.fetchrow(
                "SELECT * FROM asset_templates WHERE id = $1",
                template_id,
            )
            if tmpl_row is None:
                return None

            role_rows = await conn.fetch(
                "SELECT * FROM template_roles WHERE template_id = $1 ORDER BY sort_order",
                template_id,
            )
            kpi_rows = await conn.fetch(
                "SELECT * FROM kpi_definitions WHERE template_id = $1 ORDER BY id",
                template_id,
            )

        tmpl = _row_to_asset_template(tmpl_row)
        tmpl.roles = [_row_to_template_role(r) for r in role_rows]
        tmpl.kpi_definitions = [_row_to_kpi_definition(r) for r in kpi_rows]
        return tmpl

    # --- Asset Instance CRUD implementasyonları ---

    async def insert_asset_instance(self, instance: AssetInstance) -> AssetInstance:
        """Yeni asset instance kaydı oluşturur ve döndürür."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO asset_instances "
                "(template_id, name, description, location, status) "
                "VALUES ($1, $2, $3, $4, $5) "
                "RETURNING *",
                instance.template_id, instance.name, instance.description,
                instance.location, instance.status,
            )
        assert row is not None  # INSERT RETURNING her zaman satır döndürür
        return _row_to_asset_instance(row)

    async def update_asset_instance(
        self,
        instance_id: int,
        updates: dict[str, object],
    ) -> AssetInstance | None:
        """Asset instance kaydını günceller."""
        invalid = set(updates.keys()) - _ALLOWED_INSTANCE_UPDATE_FIELDS
        if invalid:
            msg = f"Güncellenemeyen alanlar: {invalid}"
            raise ValueError(msg)

        if not updates:
            return await self.get_asset_instance(instance_id)

        # Dinamik SET cümlesi oluştur (alan adları whitelist'ten geldiği için güvenli)
        set_parts: list[str] = []
        values: list[object] = []
        for i, (col, val) in enumerate(updates.items(), start=1):
            set_parts.append(f"{col} = ${i}")
            values.append(val)

        # updated_at'i de güncelle
        idx = len(values) + 1
        set_parts.append(f"updated_at = ${idx}")
        values.append(datetime.now(UTC))

        # WHERE koşulu
        idx_where = len(values) + 1
        values.append(instance_id)

        sql = (
            f"UPDATE asset_instances SET {', '.join(set_parts)} "
            f"WHERE id = ${idx_where} RETURNING *"
        )

        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(sql, *values)

        if row is None:
            return None
        return _row_to_asset_instance(row)

    async def delete_asset_instance(self, instance_id: int) -> bool:
        """Asset instance kaydını siler (binding'ler CASCADE ile silinir)."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM asset_instances WHERE id = $1",
                instance_id,
            )
        return str(result) == "DELETE 1"

    async def get_asset_instance(self, instance_id: int) -> AssetInstance | None:
        """Tek bir asset instance kaydını getirir."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM asset_instances WHERE id = $1",
                instance_id,
            )
        if row is None:
            return None
        return _row_to_asset_instance(row)

    async def list_asset_instances(
        self,
        template_id: int | None = None,
        status: str | None = None,
    ) -> list[AssetInstance]:
        """Asset instance listesini döndürür."""
        pool = self._get_pool()
        conditions: list[str] = []
        params: list[object] = []
        idx = 1

        if template_id is not None:
            conditions.append(f"template_id = ${idx}")
            params.append(template_id)
            idx += 1

        if status is not None:
            conditions.append(f"status = ${idx}")
            params.append(status)
            idx += 1

        where_clause = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = f"SELECT * FROM asset_instances{where_clause} ORDER BY id"

        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
        return [_row_to_asset_instance(row) for row in rows]

    # --- Tag Binding CRUD implementasyonları ---

    async def insert_tag_binding(self, binding: TagBinding) -> TagBinding:
        """Yeni tag binding kaydı oluşturur ve döndürür."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO tag_bindings (instance_id, role_id, tag_id) "
                "VALUES ($1, $2, $3) "
                "RETURNING *",
                binding.instance_id, binding.role_id, binding.tag_id,
            )
        assert row is not None  # INSERT RETURNING her zaman satır döndürür
        return _row_to_tag_binding(row)

    async def delete_tag_binding(self, binding_id: int) -> bool:
        """Tag binding kaydını siler."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM tag_bindings WHERE id = $1",
                binding_id,
            )
        return str(result) == "DELETE 1"

    async def list_tag_bindings(self, instance_id: int) -> list[TagBinding]:
        """Bir instance'ın tüm tag binding'lerini döndürür."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM tag_bindings WHERE instance_id = $1 ORDER BY id",
                instance_id,
            )
        return [_row_to_tag_binding(row) for row in rows]

    async def replace_tag_bindings(
        self,
        instance_id: int,
        bindings: list[TagBinding],
    ) -> list[TagBinding]:
        """Mevcut binding'leri silip yenileriyle değiştirir (tek transaction)."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "DELETE FROM tag_bindings WHERE instance_id = $1",
                    instance_id,
                )
                result: list[TagBinding] = []
                for b in bindings:
                    row = await conn.fetchrow(
                        "INSERT INTO tag_bindings (instance_id, role_id, tag_id) "
                        "VALUES ($1, $2, $3) RETURNING *",
                        instance_id, b.role_id, b.tag_id,
                    )
                    assert row is not None
                    result.append(_row_to_tag_binding(row))
                return result

    # --- Threshold CRUD implementasyonları ---

    async def insert_threshold(self, threshold: Threshold) -> Threshold:
        """Yeni threshold kaydı oluşturur ve döndürür."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO thresholds "
                "(tag_id, name, direction, set_point, severity, "
                "debounce_seconds, hysteresis, enabled) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8) "
                "RETURNING *",
                threshold.tag_id, threshold.name, threshold.direction,
                threshold.set_point, threshold.severity,
                threshold.debounce_seconds, threshold.hysteresis,
                threshold.enabled,
            )
        assert row is not None
        return _row_to_threshold(row)

    async def update_threshold(
        self,
        threshold_id: int,
        updates: dict[str, object],
    ) -> Threshold | None:
        """Threshold kaydını günceller."""
        invalid = set(updates.keys()) - _ALLOWED_THRESHOLD_UPDATE_FIELDS
        if invalid:
            msg = f"Güncellenemeyen alanlar: {invalid}"
            raise ValueError(msg)

        if not updates:
            return await self.get_threshold(threshold_id)

        set_parts: list[str] = []
        values: list[object] = []
        for i, (col, val) in enumerate(updates.items(), start=1):
            set_parts.append(f"{col} = ${i}")
            values.append(val)

        # updated_at'i de güncelle
        idx = len(values) + 1
        set_parts.append(f"updated_at = ${idx}")
        values.append(datetime.now(UTC))

        idx_where = len(values) + 1
        values.append(threshold_id)

        sql = (
            f"UPDATE thresholds SET {', '.join(set_parts)} "
            f"WHERE id = ${idx_where} RETURNING *"
        )

        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(sql, *values)

        if row is None:
            return None
        return _row_to_threshold(row)

    async def delete_threshold(self, threshold_id: int) -> bool:
        """Threshold kaydını siler (alarm_events CASCADE ile silinir)."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM thresholds WHERE id = $1",
                threshold_id,
            )
        return str(result) == "DELETE 1"

    async def get_threshold(self, threshold_id: int) -> Threshold | None:
        """Tek bir threshold kaydını getirir."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM thresholds WHERE id = $1",
                threshold_id,
            )
        if row is None:
            return None
        return _row_to_threshold(row)

    async def list_thresholds(
        self,
        tag_id: str | None = None,
        enabled: bool | None = None,
    ) -> list[Threshold]:
        """Threshold listesini döndürür."""
        pool = self._get_pool()
        conditions: list[str] = []
        params: list[object] = []
        idx = 1

        if tag_id is not None:
            conditions.append(f"tag_id = ${idx}")
            params.append(tag_id)
            idx += 1

        if enabled is not None:
            conditions.append(f"enabled = ${idx}")
            params.append(enabled)
            idx += 1

        where_clause = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = f"SELECT * FROM thresholds{where_clause} ORDER BY id"

        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
        return [_row_to_threshold(row) for row in rows]

    # --- Alarm Event CRUD implementasyonları ---

    async def insert_alarm_event(self, event: AlarmEvent) -> AlarmEvent:
        """Yeni alarm event kaydı oluşturur ve döndürür."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO alarm_events "
                "(threshold_id, tag_id, state, triggered_at, "
                "acknowledged_at, cleared_at, trigger_value, clear_value, notes) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9) "
                "RETURNING *",
                event.threshold_id, event.tag_id, event.state,
                event.triggered_at, event.acknowledged_at, event.cleared_at,
                event.trigger_value, event.clear_value, event.notes,
            )
        assert row is not None
        return _row_to_alarm_event(row)

    async def update_alarm_event(
        self,
        event_id: int,
        updates: dict[str, object],
    ) -> AlarmEvent | None:
        """Alarm event kaydını günceller."""
        invalid = set(updates.keys()) - _ALLOWED_ALARM_EVENT_UPDATE_FIELDS
        if invalid:
            msg = f"Güncellenemeyen alanlar: {invalid}"
            raise ValueError(msg)

        if not updates:
            return await self.get_alarm_event(event_id)

        set_parts: list[str] = []
        values: list[object] = []
        for i, (col, val) in enumerate(updates.items(), start=1):
            set_parts.append(f"{col} = ${i}")
            values.append(val)

        idx_where = len(values) + 1
        values.append(event_id)

        sql = (
            f"UPDATE alarm_events SET {', '.join(set_parts)} "
            f"WHERE id = ${idx_where} RETURNING *"
        )

        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(sql, *values)

        if row is None:
            return None
        return _row_to_alarm_event(row)

    async def get_alarm_event(self, event_id: int) -> AlarmEvent | None:
        """Tek bir alarm event kaydını getirir."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM alarm_events WHERE id = $1",
                event_id,
            )
        if row is None:
            return None
        return _row_to_alarm_event(row)

    async def list_alarm_events(
        self,
        state: str | None = None,
        tag_id: str | None = None,
        limit: int = 100,
    ) -> list[AlarmEvent]:
        """Alarm event listesini döndürür."""
        pool = self._get_pool()
        conditions: list[str] = []
        params: list[object] = []
        idx = 1

        if state is not None:
            conditions.append(f"state = ${idx}")
            params.append(state)
            idx += 1

        if tag_id is not None:
            conditions.append(f"tag_id = ${idx}")
            params.append(tag_id)
            idx += 1

        where_clause = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(limit)
        sql = (
            f"SELECT * FROM alarm_events{where_clause} "
            f"ORDER BY triggered_at DESC LIMIT ${idx}"
        )

        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
        return [_row_to_alarm_event(row) for row in rows]

    async def get_active_alarm_for_threshold(
        self,
        threshold_id: int,
    ) -> AlarmEvent | None:
        """Threshold için aktif (cleared olmayan) en son alarm döndürür."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM alarm_events "
                "WHERE threshold_id = $1 AND state != 'cleared' "
                "ORDER BY triggered_at DESC LIMIT 1",
                threshold_id,
            )
        if row is None:
            return None
        return _row_to_alarm_event(row)

    # --- Audit Log implementasyonları ---

    async def insert_audit_log(self, entry: AuditLogEntry) -> AuditLogEntry:
        """Yeni audit log kaydı oluşturur ve döndürür."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO audit_log "
                "(category, action, entity_type, entity_id, detail) "
                "VALUES ($1, $2, $3, $4, $5) "
                "RETURNING *",
                entry.category, entry.action,
                entry.entity_type, entry.entity_id, entry.detail,
            )
        assert row is not None
        return _row_to_audit_log_entry(row)

    async def list_audit_log(
        self,
        category: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditLogEntry]:
        """Audit log listesini döndürür."""
        pool = self._get_pool()
        params: list[object] = []
        idx = 1

        if category is not None:
            where_clause = f" WHERE category = ${idx}"
            params.append(category)
            idx += 1
        else:
            where_clause = ""

        params.append(limit)
        limit_idx = idx
        idx += 1
        params.append(offset)
        offset_idx = idx

        sql = (
            f"SELECT * FROM audit_log{where_clause} "
            f"ORDER BY timestamp DESC LIMIT ${limit_idx} OFFSET ${offset_idx}"
        )

        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
        return [_row_to_audit_log_entry(row) for row in rows]

    async def count_audit_log(self, category: str | None = None) -> int:
        """Audit log kayıt sayısını döndürür."""
        pool = self._get_pool()
        if category is not None:
            sql = "SELECT COUNT(*) FROM audit_log WHERE category = $1"
            async with pool.acquire() as conn:
                count = await conn.fetchval(sql, category)
        else:
            sql = "SELECT COUNT(*) FROM audit_log"
            async with pool.acquire() as conn:
                count = await conn.fetchval(sql)
        return int(count or 0)

    # --- KPI Results implementasyonları ---

    async def insert_kpi_result(self, result: KpiResult) -> KpiResult:
        """Yeni KPI sonucu kaydeder ve döndürür."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO kpi_results "
                "(instance_id, kpi_definition_id, bucket_start, value) "
                "VALUES ($1, $2, $3, $4) "
                "ON CONFLICT (instance_id, kpi_definition_id, bucket_start) "
                "DO UPDATE SET value = EXCLUDED.value "
                "RETURNING *",
                result.instance_id, result.kpi_definition_id,
                result.bucket_start, result.value,
            )
        assert row is not None
        return _row_to_kpi_result(row)

    async def insert_kpi_results_batch(self, results: list[KpiResult]) -> None:
        """Çoklu KPI sonucunu tek batch halinde yazar."""
        if not results:
            return
        pool = self._get_pool()
        async with pool.acquire() as conn:
            await conn.executemany(
                "INSERT INTO kpi_results "
                "(instance_id, kpi_definition_id, bucket_start, value) "
                "VALUES ($1, $2, $3, $4) "
                "ON CONFLICT (instance_id, kpi_definition_id, bucket_start) "
                "DO UPDATE SET value = EXCLUDED.value",
                [
                    (r.instance_id, r.kpi_definition_id, r.bucket_start, r.value)
                    for r in results
                ],
            )

    async def list_kpi_results(
        self,
        instance_id: int,
        kpi_definition_id: int | None = None,
        limit: int = 100,
    ) -> list[KpiResult]:
        """KPI sonuç listesini döndürür."""
        pool = self._get_pool()
        params: list[object] = [instance_id]
        idx = 2

        where = "WHERE instance_id = $1"
        if kpi_definition_id is not None:
            where += f" AND kpi_definition_id = ${idx}"
            params.append(kpi_definition_id)
            idx += 1

        params.append(limit)
        sql = (
            f"SELECT * FROM kpi_results {where} "
            f"ORDER BY bucket_start DESC LIMIT ${idx}"
        )

        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
        return [_row_to_kpi_result(row) for row in rows]

    async def get_latest_kpi_results(
        self,
        instance_id: int,
    ) -> dict[int, KpiResult]:
        """Her KPI definition için en son hesaplanan değeri döndürür."""
        pool = self._get_pool()
        sql = (
            "SELECT DISTINCT ON (kpi_definition_id) * "
            "FROM kpi_results WHERE instance_id = $1 "
            "ORDER BY kpi_definition_id, bucket_start DESC"
        )
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, instance_id)
        return {
            row["kpi_definition_id"]: _row_to_kpi_result(row)
            for row in rows
        }

    # --- Anomaly Scores implementasyonları ---

    async def insert_anomaly_score(self, score: AnomalyScore) -> AnomalyScore:
        """Yeni anomali skoru kaydeder ve döndürür."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO anomaly_scores "
                "(instance_id, timestamp, score, is_anomaly, feature_vector) "
                "VALUES ($1, $2, $3, $4, $5) "
                "RETURNING *",
                score.instance_id, score.timestamp,
                score.score, score.is_anomaly, score.feature_vector,
            )
        assert row is not None
        return _row_to_anomaly_score(row)

    async def list_anomaly_scores(
        self,
        instance_id: int,
        limit: int = 100,
    ) -> list[AnomalyScore]:
        """Anomali skor listesini döndürür."""
        pool = self._get_pool()
        sql = (
            "SELECT * FROM anomaly_scores WHERE instance_id = $1 "
            "ORDER BY timestamp DESC LIMIT $2"
        )
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, instance_id, limit)
        return [_row_to_anomaly_score(row) for row in rows]

    async def get_latest_anomaly_score(
        self,
        instance_id: int,
    ) -> AnomalyScore | None:
        """En son anomali skorunu döndürür."""
        pool = self._get_pool()
        sql = (
            "SELECT * FROM anomaly_scores WHERE instance_id = $1 "
            "ORDER BY timestamp DESC LIMIT 1"
        )
        async with pool.acquire() as conn:
            row = await conn.fetchrow(sql, instance_id)
        if row is None:
            return None
        return _row_to_anomaly_score(row)

    async def count_anomalies(
        self,
        since: datetime | None = None,
    ) -> int:
        """Anomali sayısını döndürür."""
        pool = self._get_pool()
        if since is not None:
            sql = (
                "SELECT COUNT(*) FROM anomaly_scores "
                "WHERE is_anomaly = TRUE AND timestamp >= $1"
            )
            async with pool.acquire() as conn:
                count = await conn.fetchval(sql, since)
        else:
            sql = "SELECT COUNT(*) FROM anomaly_scores WHERE is_anomaly = TRUE"
            async with pool.acquire() as conn:
                count = await conn.fetchval(sql)
        return int(count or 0)


    # --- Push Subscriptions implementasyonları ---

    async def upsert_push_subscription(
        self,
        sub: PushSubscription,
    ) -> PushSubscription:
        """Push subscription kaydeder veya günceller (endpoint bazlı upsert)."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO push_subscriptions "
                "(endpoint, p256dh, auth, notify_warn, notify_crit, "
                "quiet_start, quiet_end) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7) "
                "ON CONFLICT (endpoint) DO UPDATE SET "
                "p256dh = EXCLUDED.p256dh, auth = EXCLUDED.auth, "
                "updated_at = NOW() "
                "RETURNING *",
                sub.endpoint, sub.p256dh, sub.auth,
                sub.notify_warn, sub.notify_crit,
                sub.quiet_start, sub.quiet_end,
            )
        assert row is not None
        return _row_to_push_subscription(row)

    async def delete_push_subscription(self, endpoint: str) -> bool:
        """Push subscription siler. Başarılıysa True döndürür."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM push_subscriptions WHERE endpoint = $1",
                endpoint,
            )
        return str(result) == "DELETE 1"

    async def list_push_subscriptions(self) -> list[PushSubscription]:
        """Tüm push subscription'ları döndürür."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM push_subscriptions ORDER BY created_at DESC"
            )
        return [_row_to_push_subscription(row) for row in rows]

    async def update_push_subscription_settings(
        self,
        endpoint: str,
        updates: dict[str, object],
    ) -> PushSubscription | None:
        """Push subscription ayarlarını günceller. Bulunamazsa None döndürür."""
        pool = self._get_pool()
        filtered = {k: v for k, v in updates.items() if k in _ALLOWED_PUSH_SUB_UPDATE_FIELDS}
        if not filtered:
            # Güncelleme yapılacak alan yok — mevcut kaydı döndür
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM push_subscriptions WHERE endpoint = $1",
                    endpoint,
                )
            if row is None:
                return None
            return _row_to_push_subscription(row)

        set_parts: list[str] = []
        params: list[object] = []
        for idx, (col, val) in enumerate(filtered.items(), start=1):
            set_parts.append(f"{col} = ${idx}")
            params.append(val)
        idx_endpoint = len(params) + 1
        set_parts.append("updated_at = NOW()")
        params.append(endpoint)

        sql = (
            f"UPDATE push_subscriptions SET {', '.join(set_parts)} "
            f"WHERE endpoint = ${idx_endpoint} RETURNING *"
        )
        async with pool.acquire() as conn:
            row = await conn.fetchrow(sql, *params)
        if row is None:
            return None
        return _row_to_push_subscription(row)


def create_database(settings: Settings) -> DatabaseInterface:
    """Veritabanı instance'ı oluşturan factory fonksiyonu.

    Şu an her zaman TimescaleDBDatabase döndürür. Abstract tip
    döndürdüğü için ileride başka implementasyonlara geçiş kolaydır.
    """
    return TimescaleDBDatabase(settings)
