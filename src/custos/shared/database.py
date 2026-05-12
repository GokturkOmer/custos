"""Veritabanı abstract arayüzü ve TimescaleDB implementasyonu.

Mimari prensip: tüm veritabanı erişimi bu modüldeki abstract
arayüz üzerinden yapılır. Modüllerden doğrudan SQL/ORM çağrısı
yapılmaz.
"""

from __future__ import annotations

import abc
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from typing import Any, Literal

import asyncpg
import structlog

from custos.shared.config import Settings
from custos.shared.query_guard import (
    Layer,
    QueryGuardError,
    evaluate_query,
)

logger = structlog.get_logger(logger_name="database")

# Overview chart ve auto-resolution query için varsayılan hedef nokta sayısı.
# uPlot canvas render'ı ~600 nokta ile 24h × 30K okumayı ~50x hızlandırıyor.
DEFAULT_TARGET_POINTS = 600


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
    """Tag tanım kaydı — tags tablosunun Python temsili.

    P-05 (V11-108) ile stuck-at preset alanları:

    - ``stuck_at_preset``: 'auto' (default — birime göre çözülür),
      'none' (kontrol kapalı), 'fast'/'slow'/'very_slow' (hardcoded
      saniye eşikleri), 'counter' (azalma + durağanlık mantığı).
    - ``stuck_at_seconds``: Manuel saniye override; doluysa preset'in
      saniyesinin yerine geçer (preset='counter' ise mantığı korur).

    R-06 (V11-304) ile rate-of-change alanı:

    - ``rate_of_change_threshold``: Pozitif değer = mutlak |Δ değer / dk|
      eşiği. NULL = kontrol kapalı (default). Threshold engine her tick'te
      mevcut + son okuma arasındaki delta'yı hesaplar; eşik aşılırsa
      ``source='rate_of_change'`` alarmı yazar (cooldown 5 dk).

    R-07 (V11-308) ile SPC iskelet alanı:

    - ``spc_enabled``: Per-tag opt-in. Default ``False`` — pilot operatörü
      ihtiyaca göre açar. ``SPCEngine`` ilk 100 örnek (sessiz öğrenme)
      sonrası EWMA + CUSUM + MAD-score sapmalarını ``source='spc'``,
      severity='warn' alarm olarak yazar (cooldown 30 dk).
    """

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
    stuck_at_preset: str = "auto"
    stuck_at_seconds: int | None = None
    rate_of_change_threshold: float | None = None
    spc_enabled: bool = False
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
    """Asset instance kaydı — bir template'in somut kurulumu.

    P-04 ile per-instance bakım modu kolonları eklendi:

    - ``maintenance_mode_until`` NULL ise instance bakımda değil. Geçmiş bir
      değer ``expire_check_loop`` tarafından otomatik temizlenir
      (manuel kapanmadan beklemenin sınırı 60 saniye). ``None`` özel hâli
      "manuel kapatma" — süresiz bakım, kullanıcı kapatana kadar.
    - ``maintenance_reason`` operatörün girdiği zorunlu açıklama (UI form'da
      min 3 karakter); audit log detail'inde de kullanılır.
    - ``maintenance_started_by_user_id`` ON DELETE SET NULL (kullanıcı
      silindiğinde bakım kaydı kalsın, kim olduğu kaybolsun).
    - ``maintenance_started_at`` "ne zaman bakıma alındı" — UI'da kalan süre
      hesabı için until ile birlikte kullanılır.
    """

    template_id: int
    name: str
    description: str = ""
    location: str = ""
    status: str = "active"
    id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    maintenance_mode_until: datetime | None = None
    maintenance_reason: str = ""
    maintenance_started_by_user_id: int | None = None
    maintenance_started_at: datetime | None = None
    # R-04 (Migration 034): Per-instance ML inference toggle.
    # AnomalyDetector tick'te False ise instance atlanir; UI'dan
    # operator/dev manuel olarak kapatabilir (ML hub'tan).
    ml_enabled: bool = True
    # R-07 (Migration 037 / V11-307): Mode-aware iskelet. Operator manuel
    # toggle eder; AnomalyDetector tick'te ``startup``/``shutdown`` modlarinda
    # alarm yazimini atlar (false positive bombardimani engellenir),
    # ``running``/``idle`` modlarinda normal calisir. Otomatik gecis Faz 3
    # V11-303 ile gelecek.
    operating_mode: str = "running"
    operating_mode_changed_at: datetime | None = None
    operating_mode_changed_by_user_id: int | None = None


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
    """Alarm event kaydı — ISA-18.2 state machine durumu.

    ``is_test`` (P-04): bakım modunda (per-instance veya global) üretilen
    alarm bu flag ile yazılır. Bu alarm'lar push gönderilmez, alarms
    sayfasında görsel olarak ayrılır ve P-12'de anomaly detector eğitim
    setinden filtrelenir. ``threshold_engine`` bakım kontrolünden sonra
    flag'i set eder.

    P-05 (V11-108) ile alarm kaynaklarını çoğullaştırdık:

    - ``threshold_id`` artık nullable — liveness ve watchdog kaynaklı
      alarmların threshold'u yoktur.
    - ``source``: 'threshold' (default, geri uyumlu) / 'anomaly' /
      'liveness' / 'watchdog' / 'rate_of_change' / 'cross_sensor'.
      Alarm sayfası "Tip" filtresi bu alanı kullanır.
    - ``severity``: Threshold'sız alarmlarda denormalize tutulur.
      Threshold kaynaklı alarmlarda ``threshold_engine`` insert
      sırasında threshold.severity'yi explicit set eder (filtreyi
      hızlandırır, threshold silinse bile alarmın severity'si kalır).
    - ``message``: Kullanıcıya gösterilecek açıklama (ör. liveness için
      "Sensör donuk: 1820s'dir değer değişmedi").

    R-06 (V11-306) severity escalation alanları:

    - ``escalated_from``: Yükseltildiyse orijinal severity (örn. 'warn').
      NULL ise hiç yükseltilmemiş (varsayılan).
    - ``escalated_at``: Yükseltme zamanı UTC. ``escalation_loop`` set eder.

    Yükseltme ``update_alarm_event`` üzerinden yapılır — RETURNING'i
    ``_row_to_alarm_event`` parse eder; label=None default davranışı
    bozulmaz (active alarm SELECT'leri zaten LEFT JOIN ile etiketi getirir).
    """

    tag_id: str
    threshold_id: int | None = None
    state: str = "triggered"  # 'triggered' / 'acknowledged' / 'cleared'
    triggered_at: datetime | None = None
    acknowledged_at: datetime | None = None
    cleared_at: datetime | None = None
    trigger_value: float = 0.0
    clear_value: float | None = None
    notes: str = ""
    is_test: bool = False
    source: str = "threshold"
    severity: str = "warn"
    message: str = ""
    escalated_from: str | None = None
    escalated_at: datetime | None = None
    id: int | None = None
    created_at: datetime | None = None
    # R-05a: Alarm SELECT'leri LEFT JOIN ile etiketi getirir; insert/update
    # path'i etiketsiz alarm üretir (label=None geri uyumlu).
    label: AlarmEventLabel | None = None


# R-05 / V11-301 etiketleme — Migration 035 CHECK constraint'i ile aynı liste.
# Frontend (alarm satırı 4 buton) + endpoint validasyonu + helper sayım anahtarları
# bu sıralamayı kullanır. ``LABEL_CLASSES`` operatöre gösterilen doğal sıralama
# (en kritik → en az kritik); ``LABEL_CLASS_VALUES`` set lookup için.
LabelClass = Literal[
    "gercek_ariza",
    "yanlis_alarm",
    "bakim_sirasinda",
    "bilinmiyor",
]
LABEL_CLASSES: tuple[str, ...] = (
    "gercek_ariza",
    "yanlis_alarm",
    "bakim_sirasinda",
    "bilinmiyor",
)
LABEL_CLASS_VALUES: frozenset[str] = frozenset(LABEL_CLASSES)


@dataclass
class AlarmEventLabel:
    """Alarm event etiketi — R-05 / V11-301.

    Operatörün bir alarm/anomaly olayına atadığı 4 sınıftan biri. Pilot
    süresince etiketler birikir; pilot kabul sonrası V11-303 (Shadow mode +
    Auto retraining) bu etiketleri shadow inference baseline + retraining
    için kullanır.

    UNIQUE (alarm_event_id) — her alarm için tek aktif etiket. Re-label
    upsert ile mevcut satırı günceller; tarihsel re-label izi audit_log
    üzerinden tutulur (ayrı history tablosu açılmıyor).
    """

    alarm_event_id: int
    label_class: str
    labeled_by_user_id: int
    notes: str = ""
    id: int | None = None
    labeled_at: datetime | None = None


# R-06 / V11-305: Cross-sensor consistency operatörleri. Migration 036 CHECK
# constraint'i ile aynı liste; UI dropdown ve helper validasyonu burayı kullanır.
CROSS_SENSOR_OPERATORS: tuple[str, ...] = ("lt", "gt", "eq", "neq", "lte", "gte")
CROSS_SENSOR_OPERATOR_VALUES: frozenset[str] = frozenset(CROSS_SENSOR_OPERATORS)


@dataclass
class CrossSensorRule:
    """İki tag arasında mantıksal tutarlılık kuralı (R-06 / V11-305).

    Threshold engine her tick sonunda aktif kuralları tarayıp ihlal varsa
    ``source='cross_sensor'`` alarmı yazar. Kural: ``tag_a {operator} tag_b``
    olmalı; aksi alarm. Örn. ``supply_temp lt return_temp`` (chiller normal
    durumda supply < return, tersi → AC arıza belirtisi).

    ``operator`` Migration 036 CHECK constraint'i ile sınırlı (lt/gt/eq/neq/
    lte/gte). ``severity`` 4-tier (info/warn/crit/emergency); push_sender
    aynı severity filtresine girer.

    ``tag_a_id`` / ``tag_b_id`` ``tags(id)`` FK'sı (BIGINT) — tag silindiğinde
    CASCADE ile kural otomatik kalkar. Eşit tag yasak (DB CHECK).
    """

    name: str
    tag_a_id: int
    tag_b_id: int
    operator: str  # CROSS_SENSOR_OPERATORS arasından
    severity: str = "warn"
    enabled: bool = True
    description: str = ""
    id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


# R-07 / V11-307: Operator manuel toggle eden 4-tier mode enum.
# Migration 037 CHECK constraint'i ile bire bir esler.
OPERATING_MODES: tuple[str, ...] = ("running", "startup", "shutdown", "idle")
OPERATING_MODE_VALUES: frozenset[str] = frozenset(OPERATING_MODES)

# AnomalyDetector tick'te bu modlardayken alarm yazimi atlar (false positive
# bombardimani engellenir). Operator startup/shutdown sirasinda manuel toggle
# eder; normal calismaya donduğunde 'running' secer.
ANOMALY_SUPPRESSED_MODES: frozenset[str] = frozenset({"startup", "shutdown"})


@dataclass
class SpcState:
    """R-07 / V11-308: Per-tag SPC streaming state (EWMA + CUSUM + MAD).

    ``SPCEngine`` 5 dk tick'te her ``spc_enabled`` tag icin son okumayi
    alir, bu state'i guncelleyip diske yazar (server restart sonrasi
    ogrenme korunur). Algoritmalar:

    - **EWMA**: ``ewma_value`` exponentially weighted moving average,
      ``ewma_variance`` ile birlikte 3 sigma sapma kontrolu.
    - **CUSUM**: ``cusum_pos`` / ``cusum_neg`` kumulatif pozitif / negatif
      sapma; |CUSUM| > H * stddev tetikler alarm.
    - **MAD**: ``mad_median`` + ``mad_value`` (median absolute deviation);
      robust z-score = |x - median| / (1.4826 * MAD), 3.5 esiği tetikler.

    ``sample_count`` ilk 100 ornek = ogrenme penceresi (sessiz).
    ``learning_complete=True`` olduktan sonra alarmlar yazilmaya baslar.
    ``last_sample_at`` ayni timestamp'i bir daha islememek icin (idempotency).
    """

    tag_id: str
    sample_count: int = 0
    ewma_value: float | None = None
    ewma_variance: float | None = None
    cusum_pos: float = 0.0
    cusum_neg: float = 0.0
    mad_median: float | None = None
    mad_value: float | None = None
    last_sample_at: datetime | None = None
    learning_complete: bool = False
    id: int | None = None
    updated_at: datetime | None = None


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
    """Anomali skoru — anomaly_scores tablosunun Python temsili.

    Wind pivot Faz 1.3 (migration 040) ile ``engine_type`` kolonu eklendi:

    - ``'if'``: Isolation Forest (default, geri uyumlu — AVM ve wind).
    - ``'ae'``: Autoencoder (MLPRegressor sigi NN, sadece wind pivot).
    Dual-engine modunda her instance icin iki satir yazilir (her engine
    kendi engine_type'i ile).
    """

    instance_id: int
    timestamp: datetime
    score: float
    is_anomaly: bool = False
    feature_vector: str = ""
    engine_type: str = "if"
    id: int | None = None
    created_at: datetime | None = None


@dataclass
class PushSubscription:
    """Web Push bildirim aboneliği — push_subscriptions tablosunun Python temsili.

    P-03 ile çoklu alıcı kolonları: ``label`` (insana okunabilir etiket),
    ``enabled`` (tek-tıkla sustur), ``notify_info`` / ``notify_emergency``
    (4-tier severity için ayrı kolonlar — info default kapalı, emergency
    default açık), ``created_by_user_id`` (yetki: Operator sadece kendi
    aboneliğini düzenler — app-katmanında enforce edilir).
    """

    endpoint: str
    p256dh: str
    auth: str
    notify_warn: bool = True
    notify_crit: bool = True
    quiet_start: time | None = None
    quiet_end: time | None = None
    label: str = ""
    enabled: bool = True
    notify_info: bool = False
    notify_emergency: bool = True
    created_by_user_id: int | None = None
    id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class OverviewChartTag:
    """Overview grafik tag konfigurasyonu — hangi tag hangi grafikte gosterilecek."""

    chart_key: str
    tag_id: str
    sort_order: int = 0
    id: int | None = None
    created_at: datetime | None = None


@dataclass
class OverviewChart:
    """Overview dashboard'undaki bir chart slotu (kullanici tanimli)."""

    chart_key: str  # slug, PK
    title: str
    sort_order: int = 0
    time_window_minutes: int = 30
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class MaintenanceChecklistStep:
    """Bir kontrol listesinin sıralı adımı (örn. 'Filtre basıncını oku')."""

    checklist_id: int
    sort_order: int
    text: str
    estimated_minutes: int | None = None
    id: int | None = None
    created_at: datetime | None = None


@dataclass
class MaintenanceChecklist:
    """Kontrol listesi tanımı — periyodik ya da alarm senaryosunda kullanılır."""

    slug: str
    title: str
    description: str = ""
    category: str = "generic"  # 'periodic' / 'alarm' / 'generic'
    asset_template_id: int | None = None
    steps: list[MaintenanceChecklistStep] = field(default_factory=list)
    id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class MaintenanceSchedule:
    """Periyodik bakım takvimi — checklist + asset + periyot tanımı."""

    checklist_id: int
    period_kind: str  # 'daily' / 'weekly' / 'monthly' / 'yearly' / 'custom_days'
    anchor_date: date
    next_due_at: datetime
    period_value: int = 1
    asset_template_id: int | None = None
    asset_instance_id: int | None = None
    notify_lead_hours: int = 24
    enabled: bool = True
    id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class MaintenanceTask:
    """Tetiklenmiş bakım görevi — schedule, alarm veya manuel kaynaklı."""

    checklist_id: int
    source: str  # 'schedule' / 'alarm' / 'manual'
    title_snapshot: str
    schedule_id: int | None = None
    asset_instance_id: int | None = None
    alarm_event_id: int | None = None
    due_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    completed_by: str = ""
    notes: str = ""
    status: str = "pending"  # 'pending' / 'in_progress' / 'completed' / 'skipped' / 'missed'
    id: int | None = None
    created_at: datetime | None = None


@dataclass
class MaintenanceTaskStepResult:
    """Tek bir checklist adımının bir task için tamamlanma durumu."""

    task_id: int
    step_id: int
    checked: bool = False
    note: str = ""
    completed_at: datetime | None = None
    id: int | None = None


@dataclass
class AlarmChecklistMapping:
    """Bir threshold'u bir checklist ile 1:1 eşler (alarm müdahale prosedürü)."""

    threshold_id: int
    checklist_id: int
    id: int | None = None
    created_at: datetime | None = None


@dataclass
class RetentionConfig:
    """Runtime retention ayarları — singleton (id=1) satırının Python temsili.

    ``auto_clean_enabled=False`` iken TimescaleDB retention policy'si
    ``tag_readings`` ve ``features`` hypertable'larından kaldırılır; kullanıcı
    disk dolana kadar uyarı alır, veri silinmez.

    ``push_global_enabled=False`` master switch — P-03 ile eklendi. Tatil /
    eğitim sırasında tüm push'lar tek tıkla sustulur (her bir aboneliğin
    ``enabled`` flag'inden bağımsız, sender erken-dönüşü ile kestirme).

    ``global_maintenance_*`` (P-04): sistem-geneli bakım modu. Aktif iken
    tüm threshold breach'leri ``alarm_events.is_test=true`` ile yazılır,
    push gönderilmez. Singleton tabloda tutulur — runtime'da push master
    switch ile aynı pattern.
    """

    raw_retention_days: int
    auto_clean_enabled: bool
    updated_at: datetime
    updated_by: str
    push_global_enabled: bool = True
    global_maintenance_until: datetime | None = None
    global_maintenance_reason: str = ""
    global_maintenance_started_by_user_id: int | None = None
    global_maintenance_started_at: datetime | None = None
    # V11-111 / P-06: Resource alarm esikleri (CPU + RAM, range 50-99).
    # Default %90 — UI slider 70-95 aralik onerir, CHECK constraint 50-99.
    resource_cpu_warn_pct: int = 90
    resource_ram_warn_pct: int = 90
    # R-04 (Migration 034): Sistem-geneli ML inference master switch.
    # False iken AnomalyDetector tick erken doner; per-instance flag
    # irrelevant olur (push_global_enabled ile ayni desen).
    ml_inference_enabled: bool = True
    # R-06 (Migration 036 / V11-306): Severity escalation süresi — warn
    # alarmı bu kadar dakika açık kalırsa otomatik crit'e yükseltilir.
    # Default 30 dk; CHECK constraint 5-240 (DB-side).
    escalation_warn_to_crit_minutes: int = 30


@dataclass(frozen=True)
class User:
    """users tablosunun Python temsili — V11-101 auth (rol-tabanlı erişim).

    ``role`` sadece ``'operator'`` veya ``'developer'`` olabilir (DB CHECK).
    ``must_change_password`` ilk-giriş akışında True; setup.sh bootstrap +
    "Yeni Operator ekle" formu bunu True kurar.
    """

    id: int
    username: str
    role: str
    enabled: bool
    must_change_password: bool


@dataclass(frozen=True)
class Session:
    """Aktif oturum — auth dependency çıktısı (request scope).

    ``users`` ile JOIN edilmiş snapshot: dependency her request'te ek query
    atmadan ``role`` ve ``enabled`` üzerinden karar verir. Cookie token'ı
    burada yer almaz — yalnızca DB tarafında saklanır.
    """

    id: int
    user_id: int
    username: str
    role: str
    enabled: bool
    must_change_password: bool
    expires_at: datetime


@dataclass
class ServiceHeartbeat:
    """Cross-service watchdog heartbeat kaydı (V11-105/K13).

    Her servis (custos-critical, custos analytics) periyodik olarak
    ``last_heartbeat_at`` yazar. Eski heartbeat (>180s) → ölü servis;
    analytics loop bu durumu alarm olarak üretir.
    """

    service_name: str
    last_heartbeat_at: datetime
    status: str = "active"  # 'active' / 'stale' / 'down' (informational)
    metadata: dict[str, Any] | None = None


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

    @abc.abstractmethod
    async def query_tag_readings_downsampled(
        self,
        tag_id: str,
        start: datetime,
        end: datetime,
        target_points: int = DEFAULT_TARGET_POINTS,
    ) -> list[TagReading]:
        """[DEPRECATED F11] `query_readings_auto` kullan.

        Bu metot sadece ham `tag_readings` tablosu üzerinde çalışır; büyük
        pencerelerde yavaştır (1 gün+ için milyon satır taraması). Paket D
        dashboard geçişinde çağrıları `query_readings_auto`'ya taşınacak,
        v1.1 cleanup'ında silinecek.

        Tag okumalarını time_bucket ile downsample ederek döndürür.
        Bucket boyutu (end-start)/target_points'tan hesaplanır (min 1 sn).
        Boş bucket'lar döndürülmez; gerçek nokta sayısı target'tan az olabilir.
        """

    @abc.abstractmethod
    async def query_readings_auto(
        self,
        tag_id: str,
        start: datetime,
        end: datetime,
        target_points: int = DEFAULT_TARGET_POINTS,
        tag_count: int = 1,
    ) -> list[TagReading]:
        """Pencere büyüklüğüne göre doğru katmandan okur (auto-resolution).

        Katman seçimi (inclusive sınırlar):
            (end - start) <= 1 saat  → ham `tag_readings` + time_bucket downsample
            (end - start) <= 1 gün   → `tag_readings_1min` continuous aggregate
            (end - start)  > 1 gün   → `tag_readings_1hour` continuous aggregate

        Bucket boyutu her katmanda `ceil(window / target_points)` formülü
        ile hesaplanır, sonra katmanın aday listesine yuvarlanır:
            ham   : serbest saniye (min 1 sn)
            1min  : 1 / 5 / 10 / 15 dakika
            1hour : 1 / 3 / 6 / 12 saat

        Ham katman dahil TÜM katmanlar downsample eder — tüketiciye 1 Hz
        ham okuma vaat edilmez. Çıktı homojen `list[TagReading]`:
            timestamp    : bucket başlangıcı
            tag_id       : sorgulanan tag
            value        : AVG(value) bucket içinde
            quality_flag : MAX(quality_flag) bucket içinde

        Boş bucket'lar döndürülmez; gerçek nokta sayısı target'tan az olabilir.

        Guard (F11 Paket H): `tag_count × time_range_days` yükü eşiği aşarsa
        bir üst katmana zorlanır; 1hour + `time_range_days` > `query_guard_1hour_max_days`
        ise `QueryGuardError` yükselir. `tag_count` toplu sorgularda çağıran
        tarafından geçilir.
        """

    # --- Arşiv streaming (F11 Paket E) ---

    @abc.abstractmethod
    def stream_raw_readings(
        self,
        start: datetime,
        end: datetime,
        batch_size: int = 10000,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """Ham tag_readings satırlarını batch-batch streaming olarak döndürür.

        Server-side cursor ile belleğe tüm veriyi yüklemeden iterasyon yapılır.
        Parquet arşiv job'u için tasarlanmıştır. Her batch dict listesi döner:
        anahtarlar: ``timestamp``, ``tag_id``, ``value``, ``quality_flag``.
        """

    @abc.abstractmethod
    def stream_1min_aggregates(
        self,
        start: datetime,
        end: datetime,
        batch_size: int = 10000,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """tag_readings_1min continuous aggregate satırlarını streaming döndürür.

        Her batch dict listesi: ``bucket``, ``tag_id``, ``avg_value``,
        ``min_value``, ``max_value``, ``stddev_value``, ``max_quality``,
        ``sample_count``.
        """

    @abc.abstractmethod
    def stream_1hour_aggregates(
        self,
        start: datetime,
        end: datetime,
        batch_size: int = 10000,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """tag_readings_1hour continuous aggregate satırlarını streaming döndürür.

        1min ile aynı şema.
        """

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

    # --- Asset Template (read + seed upsert) ---

    @abc.abstractmethod
    async def list_asset_templates(self) -> list[AssetTemplate]:
        """Template'leri roles ve kpi_definitions ile birlikte döndürür."""

    @abc.abstractmethod
    async def get_asset_template(self, template_id: int) -> AssetTemplate | None:
        """Tekil template (roles + kpi dahil). Bulunamazsa None döndürür."""

    @abc.abstractmethod
    async def upsert_asset_template(self, template: AssetTemplate) -> AssetTemplate:
        """Asset template'i slug bazında upsert eder.

        ``roles`` (template_id, role_key) ve ``kpi_definitions`` (template_id, name)
        bazında upsert edilir. Mevcut tag_binding'leri korumak için YAML'da
        bulunmayan orphan roller silinmez. Yalnızca F9 seed akışı ve test
        fixture'ları tarafından çağrılır.
        """

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

    @abc.abstractmethod
    async def list_active_maintenance_instances(
        self,
        now: datetime,
    ) -> list[AssetInstance]:
        """Aktif bakım modunda olan instance'ları döndürür (P-04).

        Aktif: ``maintenance_started_at IS NOT NULL`` AND
        (``maintenance_mode_until IS NULL`` (manuel/sınırsız) OR
        ``maintenance_mode_until > now``).
        """

    @abc.abstractmethod
    async def list_expired_maintenance_instances(
        self,
        now: datetime,
    ) -> list[AssetInstance]:
        """Süresi dolmuş bakım modu kayıtlarını döndürür (P-04).

        Süresi dolmuş: ``maintenance_mode_until IS NOT NULL``
        AND ``maintenance_mode_until <= now``.

        ``expire_check_loop`` bu listeyi her 60 sn tarayıp otomatik kapatır.
        """

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
    async def list_tag_bindings_all(self) -> list[TagBinding]:
        """Tüm binding'leri tek query'de döndürür (tag→instance haritası).

        ``threshold_engine`` her cycle başında bakım kontrolü için tag→instance
        haritası kurar; per-instance ``list_tag_bindings`` çağrısı O(N×M)
        olurdu, tek query daha verimli (P-04).
        """

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
        is_test: bool | None = None,
        source: str | None = None,
    ) -> list[AlarmEvent]:
        """Alarm event listesini döndürür. Opsiyonel filtreler.

        ``is_test=False`` (P-04) bakım modunda üretilen alarm'ları gizler —
        alarms sayfası varsayılanı. ``is_test=True`` sadece bakım test
        alarm'larını gösterir; ``None`` (varsayılan) ikisini de döner.

        ``source`` (P-05): 'threshold' / 'anomaly' / 'liveness' / 'watchdog'
        filtresi. Alarm sayfası "Tip" dropdown'ı bu parametreyi geçer.
        """

    @abc.abstractmethod
    async def get_active_alarm_for_threshold(
        self,
        threshold_id: int,
    ) -> AlarmEvent | None:
        """Threshold için aktif (cleared olmayan) alarm döndürür."""

    # --- Alarm Event Labels (R-05 / V11-301) ---

    @abc.abstractmethod
    async def upsert_alarm_label(
        self,
        alarm_event_id: int,
        label_class: LabelClass,
        labeled_by_user_id: int,
        notes: str = "",
    ) -> AlarmEventLabel:
        """Alarmı etiketler veya mevcut etiketi günceller (alarm_event_id PK).

        ``label_class`` Migration 035 CHECK constraint ile sınırlı; ``LabelClass``
        Literal'ı dışında değer geçilirse DB ``CheckViolationError`` fırlatır.
        Re-label durumunda ``labeled_at`` NOW() ile tazelenir; eski label
        kaydının izi audit_log üzerinden tutulur.
        """

    @abc.abstractmethod
    async def get_alarm_label(self, alarm_event_id: int) -> AlarmEventLabel | None:
        """Alarmın etiketini döndürür; etiket yoksa None."""

    @abc.abstractmethod
    async def list_unlabeled_alarms(
        self,
        limit: int = 100,
    ) -> list[AlarmEvent]:
        """Etiketlenmemiş alarmları döndürür (review queue için).

        ``alarm_events LEFT JOIN alarm_event_labels`` üzerinden label_id IS NULL
        olan satırlar; ``is_test=False`` zorunlu (bakım modunda üretilen test
        alarm'larını eğitim setinden zaten çıkarıyoruz, etiketlemeye değmez).
        """

    @abc.abstractmethod
    async def count_labels_by_class(
        self,
        since: datetime | None = None,
    ) -> dict[str, int]:
        """4 sınıf için etiket sayımlarını döndürür.

        Sözlük her zaman 4 anahtara sahip; sınıf hiç kullanılmadıysa 0 döner.
        ``since`` verilirse ``labeled_at >= since`` filtresi uygulanır (ML
        hub'da "son 30 gün" kartı için).
        """

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
    async def get_push_subscription_by_endpoint(
        self,
        endpoint: str,
    ) -> PushSubscription | None:
        """Endpoint ile tek aboneliği döndürür (yetki kontrolü için)."""

    @abc.abstractmethod
    async def update_push_subscription_settings(
        self,
        endpoint: str,
        updates: dict[str, object],
    ) -> PushSubscription | None:
        """Push subscription ayarlarını günceller. Bulunamazsa None döndürür."""

    # --- Overview Charts (dinamik slot) ---

    @abc.abstractmethod
    async def list_overview_charts(self) -> list[OverviewChart]:
        """Tum overview chart slotlarini sort_order'a gore dondurur."""

    @abc.abstractmethod
    async def get_overview_chart(self, chart_key: str) -> OverviewChart | None:
        """Tek bir chart slotunu dondurur. Yoksa None."""

    @abc.abstractmethod
    async def insert_overview_chart(self, chart: OverviewChart) -> OverviewChart:
        """Yeni chart slotu ekler; chart_key cakisirsa UniqueViolation."""

    @abc.abstractmethod
    async def update_overview_chart(
        self,
        chart_key: str,
        updates: dict[str, object],
    ) -> OverviewChart | None:
        """Chart slotu alanlarini gunceller (title, sort_order, time_window_minutes)."""

    @abc.abstractmethod
    async def delete_overview_chart(self, chart_key: str) -> bool:
        """Chart slotunu siler (tag bindingleri CASCADE). Basariliysa True."""

    # --- Overview Chart Tags ---

    @abc.abstractmethod
    async def list_overview_chart_tags(
        self,
        chart_key: str | None = None,
    ) -> list[OverviewChartTag]:
        """Overview grafik tag konfigurasyonunu dondurur. chart_key verilirse filtreler."""

    @abc.abstractmethod
    async def replace_overview_chart_tags(
        self,
        chart_key: str,
        tag_ids: list[str],
    ) -> list[OverviewChartTag]:
        """Bir grafik slotunun tag listesini yenisiyle degistirir (tek transaction)."""

    # --- Maintenance Checklist CRUD ---

    @abc.abstractmethod
    async def insert_maintenance_checklist(
        self,
        checklist: MaintenanceChecklist,
    ) -> MaintenanceChecklist:
        """Yeni checklist + steps'i tek transaction ile oluşturur."""

    @abc.abstractmethod
    async def update_maintenance_checklist(
        self,
        checklist_id: int,
        updates: dict[str, object],
    ) -> MaintenanceChecklist | None:
        """Checklist alanlarını günceller (steps hariç)."""

    @abc.abstractmethod
    async def delete_maintenance_checklist(self, checklist_id: int) -> bool:
        """Checklist'i siler (steps CASCADE). Başarılıysa True."""

    @abc.abstractmethod
    async def get_maintenance_checklist(
        self,
        checklist_id: int,
    ) -> MaintenanceChecklist | None:
        """Tekil checklist + steps'i döndürür. Bulunamazsa None."""

    @abc.abstractmethod
    async def list_maintenance_checklists(
        self,
        category: str | None = None,
    ) -> list[MaintenanceChecklist]:
        """Checklist listesi (steps dahil)."""

    @abc.abstractmethod
    async def replace_maintenance_checklist_steps(
        self,
        checklist_id: int,
        steps: list[MaintenanceChecklistStep],
    ) -> list[MaintenanceChecklistStep]:
        """Bir checklist'in tüm adımlarını yenileriyle değiştirir (tek transaction)."""

    # --- Maintenance Schedule CRUD ---

    @abc.abstractmethod
    async def insert_maintenance_schedule(
        self,
        schedule: MaintenanceSchedule,
    ) -> MaintenanceSchedule:
        """Yeni periyodik bakım takvimi kaydı oluşturur."""

    @abc.abstractmethod
    async def update_maintenance_schedule(
        self,
        schedule_id: int,
        updates: dict[str, object],
    ) -> MaintenanceSchedule | None:
        """Schedule alanlarını günceller."""

    @abc.abstractmethod
    async def delete_maintenance_schedule(self, schedule_id: int) -> bool:
        """Schedule'ı siler. Başarılıysa True."""

    @abc.abstractmethod
    async def get_maintenance_schedule(
        self,
        schedule_id: int,
    ) -> MaintenanceSchedule | None:
        """Tekil schedule'ı döndürür."""

    @abc.abstractmethod
    async def list_maintenance_schedules(
        self,
        enabled: bool | None = None,
    ) -> list[MaintenanceSchedule]:
        """Schedule listesi. enabled filtresi opsiyonel."""

    @abc.abstractmethod
    async def list_due_maintenance_schedules(
        self,
        now: datetime,
    ) -> list[MaintenanceSchedule]:
        """Scheduler için — next_due_at <= now ve enabled=TRUE olan schedule'lar."""

    # --- Maintenance Task CRUD ---

    @abc.abstractmethod
    async def insert_maintenance_task(
        self,
        task: MaintenanceTask,
    ) -> MaintenanceTask:
        """Yeni maintenance task kaydı oluşturur."""

    @abc.abstractmethod
    async def update_maintenance_task(
        self,
        task_id: int,
        updates: dict[str, object],
    ) -> MaintenanceTask | None:
        """Task alanlarını günceller (status, completed_at, notes vb.)."""

    @abc.abstractmethod
    async def get_maintenance_task(
        self,
        task_id: int,
    ) -> MaintenanceTask | None:
        """Tekil task'ı döndürür."""

    @abc.abstractmethod
    async def list_upcoming_maintenance_tasks(
        self,
        within_hours: int = 48,
    ) -> list[MaintenanceTask]:
        """Önümüzdeki X saat içinde due olan pending task'lar (Overview widget)."""

    @abc.abstractmethod
    async def list_recent_maintenance_tasks(
        self,
        limit: int = 50,
    ) -> list[MaintenanceTask]:
        """Son tamamlanmış/atlanmış/missed task'lar (Geçmiş sekmesi)."""

    @abc.abstractmethod
    async def list_maintenance_tasks_for_schedule(
        self,
        schedule_id: int,
    ) -> list[MaintenanceTask]:
        """Bir schedule'a bağlı tüm task'lar."""

    # --- Maintenance Task Step Result ---

    @abc.abstractmethod
    async def upsert_maintenance_task_step_result(
        self,
        result: MaintenanceTaskStepResult,
    ) -> MaintenanceTaskStepResult:
        """Task + step kombinasyonu için sonuç ekler/günceller (UNIQUE constraint)."""

    @abc.abstractmethod
    async def list_maintenance_task_step_results(
        self,
        task_id: int,
    ) -> list[MaintenanceTaskStepResult]:
        """Bir task'ın tüm step sonuçlarını döndürür."""

    # --- Alarm Checklist Mapping ---

    @abc.abstractmethod
    async def upsert_alarm_checklist_mapping(
        self,
        threshold_id: int,
        checklist_id: int,
    ) -> AlarmChecklistMapping:
        """Threshold → checklist eşlemesi ekler/günceller (1:1)."""

    @abc.abstractmethod
    async def delete_alarm_checklist_mapping(self, threshold_id: int) -> bool:
        """Threshold'un checklist eşlemesini kaldırır."""

    @abc.abstractmethod
    async def get_alarm_checklist_mapping(
        self,
        threshold_id: int,
    ) -> AlarmChecklistMapping | None:
        """Threshold için eşlenen checklist (varsa)."""

    @abc.abstractmethod
    async def list_alarm_checklist_mappings(self) -> list[AlarmChecklistMapping]:
        """Tüm alarm → checklist eşlemelerini döndürür."""

    @abc.abstractmethod
    async def count_alarm_events_for_threshold(
        self,
        threshold_id: int,
        since: datetime,
    ) -> int:
        """Bir threshold'un verilen zamandan sonra tetiklenme sayısı."""

    # --- Retention Config (F11 Paket F) ---

    @abc.abstractmethod
    async def get_retention_config(self) -> RetentionConfig:
        """Singleton retention ayarlarını döndürür."""

    @abc.abstractmethod
    async def update_retention_config(
        self,
        raw_retention_days: int | None = None,
        auto_clean_enabled: bool | None = None,
        updated_by: str = "user",
        push_global_enabled: bool | None = None,
        resource_cpu_warn_pct: int | None = None,
        resource_ram_warn_pct: int | None = None,
        ml_inference_enabled: bool | None = None,
        escalation_warn_to_crit_minutes: int | None = None,
    ) -> RetentionConfig:
        """retention_config satırını ve TimescaleDB policy'yi senkron günceller.

        - ``auto_clean_enabled=False`` → ``tag_readings`` / ``features``
          hypertable'larından retention policy kaldırılır.
        - ``auto_clean_enabled=True`` → policy yeniden kurulur; varsa önce
          remove edilir (idempotent).
        - ``push_global_enabled`` (P-03 master switch) sadece satıra yazılır;
          runtime'da push_sender erken-dönüşle okur.
        - ``raw_retention_days`` değişmişse ve auto-clean açıksa policy
          yeni aralıkla tekrar eklenir.
        - ``resource_cpu_warn_pct`` / ``resource_ram_warn_pct`` (V11-111 / P-06):
          ResourceMonitor tick'te okur. CHECK constraint 50-99 (migration 033).
        - ``ml_inference_enabled`` (R-04 / Migration 034): AnomalyDetector
          sistem-geneli master switch; push master switch ile aynı desen.
        - ``escalation_warn_to_crit_minutes`` (R-06 / Migration 036):
          ``escalation_loop`` warn alarm'ı bu kadar dakika sonra crit'e
          yükseltir. CHECK constraint 5-240 (DB-side); UI 5-240 doğrular.

        Global maintenance kolonları burada güncellenmez —
        ``update_global_maintenance`` ile yönetilir (concern ayrımı).

        DB satırı + policy güncellemesi tek transaction içinde yapılır.
        """

    @abc.abstractmethod
    async def update_global_maintenance(
        self,
        until: datetime | None,
        reason: str,
        user_id: int | None,
        started_at: datetime | None,
    ) -> RetentionConfig:
        """Global bakım modu kolonlarını singleton retention_config satırına yazar.

        Başlatma: ``started_at = now()``, ``until = now()+delta`` (veya
        manuel için None), ``reason`` zorunlu, ``user_id`` operatör/dev id.
        Durdurma: dört alan da ``None``/``""`` ile çağrılır → bakım kapanır.

        Push/retention alanları korunur (sadece global_maintenance_*
        kolonları yazılır).
        """

    # --- Auth: users + sessions (V11-101) ---

    @abc.abstractmethod
    async def create_user(
        self,
        username: str,
        password_hash: str,
        role: str,
        must_change_password: bool = False,
    ) -> User:
        """Yeni kullanıcı oluşturur. ``role`` 'operator' veya 'developer'."""

    @abc.abstractmethod
    async def get_user_by_username(self, username: str) -> User | None:
        """Username üzerinden kullanıcıyı döndürür (login için)."""

    @abc.abstractmethod
    async def get_user_password_hash(self, username: str) -> str | None:
        """Login akışı için sadece hash'i döndürür (User snapshot dışında)."""

    @abc.abstractmethod
    async def update_user_password(
        self,
        user_id: int,
        new_password_hash: str,
    ) -> bool:
        """Kullanıcının parolasını günceller, ``must_change_password=False`` set eder.

        Dönüş: kayıt bulunduysa True.
        """

    @abc.abstractmethod
    async def update_last_login(self, user_id: int) -> None:
        """``last_login_at = NOW()`` set eder (login başarılı sonrası)."""

    @abc.abstractmethod
    async def set_user_enabled(self, user_id: int, enabled: bool) -> bool:
        """Kullanıcıyı devre dışı/aktif yapar. Devre dışı kullanıcı login olamaz."""

    @abc.abstractmethod
    async def list_users(self) -> list[User]:
        """Tüm kullanıcıları döndürür (Settings → Kullanıcılar sayfası)."""

    @abc.abstractmethod
    async def create_session(
        self,
        user_id: int,
        token: str,
        expires_at: datetime,
        ip_addr: str = "",
        user_agent: str = "",
    ) -> Session:
        """Aktif oturum kaydı oluşturur. Cookie token DB'de UNIQUE."""

    @abc.abstractmethod
    async def get_session_by_token(self, token: str) -> Session | None:
        """Cookie token'ı geçerli session'a çevirir (JOIN users → role).

        Süresi geçmiş veya kullanıcısı devre dışı session için ``None``
        döner — auth dependency cookie'yi temizler.
        """

    @abc.abstractmethod
    async def delete_session(self, token: str) -> bool:
        """Logout — token'ı siler. Dönüş: kayıt bulunduysa True."""

    @abc.abstractmethod
    async def cleanup_expired_sessions(self) -> int:
        """Süresi dolmuş session'ları siler. Dönüş: silinen kayıt sayısı."""

    @abc.abstractmethod
    async def count_recent_failed_logins(
        self,
        ip_address: str,
        since: datetime,
    ) -> int:
        """Belirtilen IP'den ``since`` tarihinden bu yana yapılan başarısız
        login sayısı (PP-06 — login rate limiting).

        ``audit_log`` tablosunda ``category='auth' AND action='login_failed'``
        kayıtlarını sayar. Detail formatı: ``ip=<addr> user=<name> reason=<r>``.
        """

    @abc.abstractmethod
    async def count_recent_failed_logins_by_username(
        self,
        username: str,
        since: datetime,
    ) -> int:
        """H-2 (29 Nis 2026 denetim) — username başına başarısız login sayısı.

        IP-bazlı sayımı tamamlar: tek hesaba dağıtık brute-force (her istek
        farklı IP) bypass edemesin diye. ``audit_log.detail`` içinde
        ``user=<username>`` token'ı LIKE pattern'iyle aranır; LIKE
        wildcard'ları (``%``, ``_``, ``\\``) ``ESCAPE '\\'`` ile kaçırılır.
        """

    # --- Service Heartbeats (V11-105/K13) ---

    @abc.abstractmethod
    async def write_service_heartbeat(
        self,
        service_name: str,
        status: str = "active",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Service heartbeat'i upsert eder (last_heartbeat_at = NOW())."""

    @abc.abstractmethod
    async def list_service_heartbeats(self) -> list[ServiceHeartbeat]:
        """Tüm servis heartbeat'lerini döner — cross-service watchdog kontrolü için."""

    # --- Cross-Sensor Rules (R-06 / V11-305) ---

    @abc.abstractmethod
    async def insert_cross_sensor_rule(
        self,
        rule: CrossSensorRule,
    ) -> CrossSensorRule:
        """Yeni cross-sensor kuralı oluşturur ve döndürür."""

    @abc.abstractmethod
    async def update_cross_sensor_rule(
        self,
        rule_id: int,
        updates: dict[str, object],
    ) -> CrossSensorRule | None:
        """Kuralı günceller. Bulunamazsa None."""

    @abc.abstractmethod
    async def delete_cross_sensor_rule(self, rule_id: int) -> bool:
        """Kuralı siler. Başarılıysa True."""

    @abc.abstractmethod
    async def get_cross_sensor_rule(
        self,
        rule_id: int,
    ) -> CrossSensorRule | None:
        """Tek kuralı döndürür."""

    @abc.abstractmethod
    async def list_cross_sensor_rules(
        self,
        enabled: bool | None = None,
    ) -> list[CrossSensorRule]:
        """Kural listesi. ``enabled=True`` engine cache'i için."""

    # --- SPC State (R-07 / V11-308) ---

    @abc.abstractmethod
    async def get_spc_state(self, tag_id: str) -> SpcState | None:
        """Tek tag icin SPC state kaydini getirir. Bulunamazsa None.

        ``SPCEngine`` her tick'te aktif tag listesini gezer; ilk goruldugunde
        ``upsert_spc_state`` ile yeni satir yazar.
        """

    @abc.abstractmethod
    async def upsert_spc_state(self, state: SpcState) -> SpcState:
        """SPC state'ini ekler ya da gunceller (tag_id UNIQUE).

        Engine her tick sonu state'i diske yazar — server restart sonrasi
        ogrenme penceresi korunsun. INSERT ON CONFLICT (tag_id) DO UPDATE
        deseni; ``updated_at`` otomatik ``NOW()``.
        """

    @abc.abstractmethod
    async def list_spc_states(self) -> list[SpcState]:
        """Tum SPC state kayitlarini doner — ML hub istatistikleri ve engine
        warm-up icin."""


# İzin verilen güncelleme alanları — Connection Profile (SQL injection önlemi)
_ALLOWED_PROFILE_UPDATE_FIELDS: frozenset[str] = frozenset(
    {
        "name",
        "host",
        "port",
        "unit_id_start",
        "unit_id_end",
        "status",
        "last_scan_at",
        "slave_latency_min_ms",
        "slave_latency_avg_ms",
        "slave_latency_max_ms",
    }
)


def _pick_bucket(desired: int, candidates: list[int]) -> int:
    """Aday listesinden `desired`'a eşit veya büyük en küçük değeri seç.

    Hepsinden büyükse maksimumu döndür (clamp). F11 Paket C auto-resolution
    query için bucket seçim yardımcısı — 1/5/10/15 dk ya da 1/3/6/12 saat.
    """
    for c in candidates:
        if desired <= c:
            return c
    return candidates[-1]


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
            float(row["slave_latency_min_ms"]) if row["slave_latency_min_ms"] is not None else None
        ),
        slave_latency_avg_ms=(
            float(row["slave_latency_avg_ms"]) if row["slave_latency_avg_ms"] is not None else None
        ),
        slave_latency_max_ms=(
            float(row["slave_latency_max_ms"]) if row["slave_latency_max_ms"] is not None else None
        ),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


# İzin verilen güncelleme alanları — Tag (SQL injection önlemi)
_ALLOWED_TAG_UPDATE_FIELDS: frozenset[str] = frozenset(
    {
        "name",
        "modbus_host",
        "modbus_port",
        "unit_id",
        "register_address",
        "register_type",
        "byte_order",
        "gain",
        "offset",
        "unit",
        "polling_interval_ms",
        "polling_preset",
        "status",
        "stuck_at_preset",
        "stuck_at_seconds",
        # R-06 (Migration 036): Rate-of-change esiği. NULL = devre dısı,
        # pozitif değer = mutlak |Δ/dk| esik. Threshold engine her tick'te
        # değerlendirir; cooldown 5 dakika.
        "rate_of_change_threshold",
        # R-07 (Migration 037): Per-tag SPC opt-in. False (default) iken
        # SPC engine bu tag'i isleminez; True'da ogrenme penceresi sonrasi
        # sapma alarmlari yazar.
        "spc_enabled",
    }
)


def _row_to_tag_record(row: asyncpg.Record) -> TagRecord:
    """asyncpg satırını TagRecord'a dönüştürür."""
    raw_rate = row["rate_of_change_threshold"]
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
        stuck_at_preset=row["stuck_at_preset"],
        stuck_at_seconds=row["stuck_at_seconds"],
        rate_of_change_threshold=float(raw_rate) if raw_rate is not None else None,
        spc_enabled=bool(row["spc_enabled"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


# İzin verilen güncelleme alanları — Asset Instance (SQL injection önlemi).
# P-04 ile maintenance_* alanları eklendi; bakım modu start/stop bu metot
# üzerinden update edilir (ayrı UPDATE SQL yazmamak için).
_ALLOWED_INSTANCE_UPDATE_FIELDS: frozenset[str] = frozenset(
    {
        "name",
        "description",
        "location",
        "status",
        "maintenance_mode_until",
        "maintenance_reason",
        "maintenance_started_by_user_id",
        "maintenance_started_at",
        # R-04: ML hub'tan toggle ile guncellenir.
        "ml_enabled",
        # R-07 (V11-307): Mode-aware iskelet — process_detail mode toggle
        # endpoint'i bu uc alani tek update'te yazar.
        "operating_mode",
        "operating_mode_changed_at",
        "operating_mode_changed_by_user_id",
    }
)


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
        maintenance_mode_until=row["maintenance_mode_until"],
        maintenance_reason=row["maintenance_reason"],
        maintenance_started_by_user_id=row["maintenance_started_by_user_id"],
        maintenance_started_at=row["maintenance_started_at"],
        ml_enabled=bool(row["ml_enabled"]),
        operating_mode=row["operating_mode"],
        operating_mode_changed_at=row["operating_mode_changed_at"],
        operating_mode_changed_by_user_id=row["operating_mode_changed_by_user_id"],
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
_ALLOWED_THRESHOLD_UPDATE_FIELDS: frozenset[str] = frozenset(
    {
        "name",
        "direction",
        "set_point",
        "severity",
        "debounce_seconds",
        "hysteresis",
        "enabled",
    }
)

# İzin verilen güncelleme alanları — Alarm Event (SQL injection önlemi).
# R-06 (Migration 036): Severity escalation için ``severity``, ``escalated_from``,
# ``escalated_at`` eklendi. ``escalation_loop`` warn alarm'ı crit'e yükseltirken
# bu üç alanı tek update'te yazar; whitelist'e eklenmezse engine ValueError
# fırlatır.
_ALLOWED_ALARM_EVENT_UPDATE_FIELDS: frozenset[str] = frozenset(
    {
        "state",
        "acknowledged_at",
        "cleared_at",
        "clear_value",
        "notes",
        "severity",
        "escalated_from",
        "escalated_at",
    }
)

# R-05a: Alarm sayfası SELECT'lerinin ortak projeksiyonu — alarm_events satırı +
# (varsa) alarm_event_labels kolonları, çakışmayı önlemek için label_* alias'ı
# ile. WHERE/ORDER BY/LIMIT çağrı yerinde eklenir; tüm filtreler ``a.``
# qualifier'ı ile yazılır. UNIQUE (alarm_event_id) constraint'i btree indeksi
# açtığı için JOIN sub-millisecond.
_ALARM_EVENT_LABEL_JOIN_SELECT: str = (
    "SELECT a.*, "
    "l.id AS label_id, "
    "l.label_class AS label_class, "
    "l.labeled_by_user_id AS labeled_by_user_id, "
    "l.labeled_at AS labeled_at, "
    "l.notes AS label_notes "
    "FROM alarm_events a "
    "LEFT JOIN alarm_event_labels l ON l.alarm_event_id = a.id"
)


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
    """asyncpg satırını AlarmEvent'e dönüştürür.

    R-06 (Migration 036): ``escalated_from`` / ``escalated_at`` kolonları
    burada okunur — eklenmez ise escalation_loop update'i sonrası dönen
    satırda kolonlar AlarmEvent'e aktarılmaz, UI rozet kaybolur.
    """
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
        is_test=bool(row["is_test"]),
        source=row["source"],
        severity=row["severity"],
        message=row["message"],
        escalated_from=row["escalated_from"],
        escalated_at=row["escalated_at"],
        created_at=row["created_at"],
    )


def _row_to_alarm_event_label(row: asyncpg.Record) -> AlarmEventLabel:
    """asyncpg satırını AlarmEventLabel'a dönüştürür."""
    return AlarmEventLabel(
        id=row["id"],
        alarm_event_id=row["alarm_event_id"],
        label_class=row["label_class"],
        labeled_by_user_id=row["labeled_by_user_id"],
        labeled_at=row["labeled_at"],
        notes=row["notes"],
    )


def _row_to_alarm_event_with_label(row: asyncpg.Record) -> AlarmEvent:
    """LEFT JOIN sonucu — alarm_events kolonları + (varsa) label kolonları.

    R-05a: alarm sayfası SELECT'leri ``alarm_events LEFT JOIN
    alarm_event_labels`` ile tek round-trip'te hem alarmı hem etiketi
    getirir. ``label_id`` NULL ise alarm etiketsizdir (``label=None``).
    Etiket kolonları çakışma yaratmaması için ``label_*`` ön ekiyle
    seçilir (``label_id``, ``label_class``, ``labeled_by_user_id``,
    ``labeled_at``, ``label_notes``).
    """
    event = _row_to_alarm_event(row)
    if row["label_id"] is not None:
        event.label = AlarmEventLabel(
            id=row["label_id"],
            alarm_event_id=row["id"],
            label_class=row["label_class"],
            labeled_by_user_id=row["labeled_by_user_id"],
            labeled_at=row["labeled_at"],
            notes=row["label_notes"],
        )
    return event


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
    """asyncpg satırını AnomalyScore'a dönüştürür.

    Migration 040 öncesi DB durumu icin defensive: ``engine_type`` kolon
    yoksa 'if' fallback (KeyError yutuluyor) — geri uyumlu.
    """
    try:
        engine_type = row["engine_type"]
    except (KeyError, IndexError):
        engine_type = "if"
    return AnomalyScore(
        id=row["id"],
        instance_id=row["instance_id"],
        timestamp=row["timestamp"],
        score=row["score"],
        is_anomaly=row["is_anomaly"],
        feature_vector=row["feature_vector"],
        engine_type=engine_type,
        created_at=row["created_at"],
    )


# İzin verilen güncelleme alanları — Push Subscription (SQL injection önlemi).
# P-03 ile genişledi: label / enabled / notify_info / notify_emergency.
# ``endpoint``, ``created_by_user_id``, ``p256dh``, ``auth`` whitelist dışında
# tutuluyor — bunlar abonelik kimliği veya altyapı verisi, kullanıcı UI'dan
# değiştiremez.
_ALLOWED_PUSH_SUB_UPDATE_FIELDS: frozenset[str] = frozenset(
    {
        "notify_warn",
        "notify_crit",
        "notify_info",
        "notify_emergency",
        "quiet_start",
        "quiet_end",
        "label",
        "enabled",
    }
)


def _row_to_push_subscription(row: asyncpg.Record) -> PushSubscription:
    """asyncpg satırını PushSubscription'a dönüştürür."""
    return PushSubscription(
        id=row["id"],
        endpoint=row["endpoint"],
        p256dh=row["p256dh"],
        auth=row["auth"],
        notify_warn=row["notify_warn"],
        notify_crit=row["notify_crit"],
        notify_info=row["notify_info"],
        notify_emergency=row["notify_emergency"],
        quiet_start=row["quiet_start"],
        quiet_end=row["quiet_end"],
        label=row["label"],
        enabled=row["enabled"],
        created_by_user_id=row["created_by_user_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


# İzin verilen güncelleme alanları — Cross-Sensor Rule (R-06 / V11-305).
# ``tag_a_id`` ve ``tag_b_id`` whitelist dışında — değiştirmek mantıksız;
# yeni kural açmak daha güvenli.
_ALLOWED_CROSS_SENSOR_UPDATE_FIELDS: frozenset[str] = frozenset(
    {
        "name",
        "operator",
        "severity",
        "enabled",
        "description",
    }
)


def _row_to_cross_sensor_rule(row: asyncpg.Record) -> CrossSensorRule:
    """asyncpg satırını CrossSensorRule'a dönüştürür."""
    return CrossSensorRule(
        id=row["id"],
        name=row["name"],
        tag_a_id=int(row["tag_a_id"]),
        tag_b_id=int(row["tag_b_id"]),
        operator=row["operator"],
        severity=row["severity"],
        enabled=bool(row["enabled"]),
        description=row["description"],
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

    async def query_tag_readings_downsampled(
        self,
        tag_id: str,
        start: datetime,
        end: datetime,
        target_points: int = DEFAULT_TARGET_POINTS,
    ) -> list[TagReading]:
        """[DEPRECATED F11] `query_readings_auto` kullan.

        Sadece ham tag_readings üzerinde time_bucket AVG. Büyük pencerelerde
        yavaştır — auto-resolution yerine bunu çağırmak aggregate'lerin
        sağladığı kazancı kaybeder. Paket D'de tüketici geçiyor, v1.1'de siliniyor.

        Bucket değeri (end-start)/target_points, min 1 sn. Quality flag
        bucket içinde MAX (en kötü/en yüksek flag korunur).
        """
        return await self._query_raw_downsampled(tag_id, start, end, target_points)

    async def query_readings_auto(
        self,
        tag_id: str,
        start: datetime,
        end: datetime,
        target_points: int = DEFAULT_TARGET_POINTS,
        tag_count: int = 1,
    ) -> list[TagReading]:
        """Pencere büyüklüğüne göre ham / 1min / 1hour katmanından okur.

        Eşikler inclusive: tam 1 saat ham'dan, tam 1 gün 1min'den döner.
        Çıktı homojen `list[TagReading]` — tüketici katman farkını bilmez.

        Query guard (F11 Paket H): `tag_count × time_range_days` yüküne göre
        aşırı geniş sorgu bir üst katmana zorlanır ya da `QueryGuardError`
        ile reddedilir. `tag_count` toplu sorgularda çağıran tarafından
        geçilir (default 1 — tek tag).
        """
        window_sec = (end - start).total_seconds()
        if window_sec <= 3600.0:
            initial_layer: Layer = "raw"
        elif window_sec <= 86400.0:
            initial_layer = "1min"
        else:
            initial_layer = "1hour"

        time_range_days = window_sec / 86400.0
        decision = evaluate_query(
            tag_count=tag_count,
            time_range_days=time_range_days,
            requested_layer=initial_layer,
            settings_obj=self._settings,
        )
        if not decision.allowed:
            raise QueryGuardError(decision.reason)

        layer: Layer = decision.forced_aggregate or initial_layer
        if layer == "raw":
            return await self._query_raw_downsampled(tag_id, start, end, target_points)
        if layer == "1min":
            return await self._query_1min_downsampled(tag_id, start, end, target_points)
        return await self._query_1hour_downsampled(tag_id, start, end, target_points)

    async def _query_raw_downsampled(
        self,
        tag_id: str,
        start: datetime,
        end: datetime,
        target_points: int,
    ) -> list[TagReading]:
        """Ham tag_readings üzerinde time_bucket AVG + MAX(quality).

        Bucket = ceil((end-start)/target_points) saniye, min 1 sn.
        """
        total_sec = max(1.0, (end - start).total_seconds())
        safe_target = max(1, target_points)
        # ceil(total_sec / safe_target)
        bucket_sec = max(1, -(-int(total_sec) // safe_target))
        pool = self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT time_bucket(make_interval(secs => $4), timestamp) AS bucket, "
                "       AVG(value) AS avg_value, "
                "       MAX(quality_flag) AS max_quality "
                "FROM tag_readings "
                "WHERE tag_id = $1 AND timestamp >= $2 AND timestamp <= $3 "
                "GROUP BY bucket ORDER BY bucket ASC",
                tag_id,
                start,
                end,
                bucket_sec,
            )
        return [
            TagReading(
                timestamp=row["bucket"],
                tag_id=tag_id,
                value=float(row["avg_value"]),
                quality_flag=int(row["max_quality"]),
            )
            for row in rows
        ]

    async def _query_1min_downsampled(
        self,
        tag_id: str,
        start: datetime,
        end: datetime,
        target_points: int,
    ) -> list[TagReading]:
        """tag_readings_1min CA üzerinde re-bucket + AVG(avg_value).

        Bucket adayları: 1, 5, 10, 15 dakika. `ceil(window_min/target)`
        hesaplanır, aday listeden en küçük eşit-veya-büyük seçilir.
        """
        window_min = max(1, int(-(-int((end - start).total_seconds()) // 60)))
        safe_target = max(1, target_points)
        desired = max(1, -(-window_min // safe_target))
        bucket_min = _pick_bucket(desired, [1, 5, 10, 15])
        pool = self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT time_bucket(make_interval(mins => $4), bucket) AS bkt, "
                "       AVG(avg_value) AS avg_value, "
                "       MAX(max_quality) AS max_quality "
                "FROM tag_readings_1min "
                "WHERE tag_id = $1 AND bucket >= $2 AND bucket <= $3 "
                "GROUP BY bkt ORDER BY bkt ASC",
                tag_id,
                start,
                end,
                bucket_min,
            )
        return [
            TagReading(
                timestamp=row["bkt"],
                tag_id=tag_id,
                value=float(row["avg_value"]),
                quality_flag=int(row["max_quality"]),
            )
            for row in rows
        ]

    async def _query_1hour_downsampled(
        self,
        tag_id: str,
        start: datetime,
        end: datetime,
        target_points: int,
    ) -> list[TagReading]:
        """tag_readings_1hour CA üzerinde re-bucket + AVG(avg_value).

        Bucket adayları: 1, 3, 6, 12 saat.
        """
        window_h = max(1, int(-(-int((end - start).total_seconds()) // 3600)))
        safe_target = max(1, target_points)
        desired = max(1, -(-window_h // safe_target))
        bucket_h = _pick_bucket(desired, [1, 3, 6, 12])
        pool = self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT time_bucket(make_interval(hours => $4), bucket) AS bkt, "
                "       AVG(avg_value) AS avg_value, "
                "       MAX(max_quality) AS max_quality "
                "FROM tag_readings_1hour "
                "WHERE tag_id = $1 AND bucket >= $2 AND bucket <= $3 "
                "GROUP BY bkt ORDER BY bkt ASC",
                tag_id,
                start,
                end,
                bucket_h,
            )
        return [
            TagReading(
                timestamp=row["bkt"],
                tag_id=tag_id,
                value=float(row["avg_value"]),
                quality_flag=int(row["max_quality"]),
            )
            for row in rows
        ]

    # --- Arşiv streaming (F11 Paket E) ---

    async def stream_raw_readings(
        self,
        start: datetime,
        end: datetime,
        batch_size: int = 10000,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """Ham tag_readings satırlarını server-side cursor ile batch streaming döndürür.

        Milyonlarca satır içeren aylık arşiv için belleğe yüklemeden iterasyon.
        Cursor ``async with conn.transaction()`` içinde açılır (asyncpg şartı);
        satır satır iterate edilip ``batch_size`` dolunca yield edilir.
        """
        pool = self._get_pool()
        async with pool.acquire() as conn, conn.transaction():
            batch: list[dict[str, Any]] = []
            async for r in conn.cursor(
                "SELECT timestamp, tag_id, value, quality_flag "
                "FROM tag_readings "
                "WHERE timestamp >= $1 AND timestamp < $2 "
                "ORDER BY timestamp ASC",
                start,
                end,
                prefetch=batch_size,
            ):
                batch.append(
                    {
                        "timestamp": r["timestamp"],
                        "tag_id": r["tag_id"],
                        "value": float(r["value"]),
                        "quality_flag": int(r["quality_flag"]),
                    }
                )
                if len(batch) >= batch_size:
                    yield batch
                    batch = []
            if batch:
                yield batch

    async def stream_1min_aggregates(
        self,
        start: datetime,
        end: datetime,
        batch_size: int = 10000,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """tag_readings_1min CA satırlarını streaming döndürür."""
        pool = self._get_pool()
        async with pool.acquire() as conn, conn.transaction():
            batch: list[dict[str, Any]] = []
            async for r in conn.cursor(
                "SELECT bucket, tag_id, avg_value, min_value, max_value, "
                "       stddev_value, max_quality, sample_count "
                "FROM tag_readings_1min "
                "WHERE bucket >= $1 AND bucket < $2 "
                "ORDER BY bucket ASC",
                start,
                end,
                prefetch=batch_size,
            ):
                batch.append(
                    {
                        "bucket": r["bucket"],
                        "tag_id": r["tag_id"],
                        "avg_value": float(r["avg_value"]),
                        "min_value": float(r["min_value"]),
                        "max_value": float(r["max_value"]),
                        "stddev_value": (
                            float(r["stddev_value"]) if r["stddev_value"] is not None else None
                        ),
                        "max_quality": int(r["max_quality"]),
                        "sample_count": int(r["sample_count"]),
                    }
                )
                if len(batch) >= batch_size:
                    yield batch
                    batch = []
            if batch:
                yield batch

    async def stream_1hour_aggregates(
        self,
        start: datetime,
        end: datetime,
        batch_size: int = 10000,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """tag_readings_1hour CA satırlarını streaming döndürür."""
        pool = self._get_pool()
        async with pool.acquire() as conn, conn.transaction():
            batch: list[dict[str, Any]] = []
            async for r in conn.cursor(
                "SELECT bucket, tag_id, avg_value, min_value, max_value, "
                "       stddev_value, max_quality, sample_count "
                "FROM tag_readings_1hour "
                "WHERE bucket >= $1 AND bucket < $2 "
                "ORDER BY bucket ASC",
                start,
                end,
                prefetch=batch_size,
            ):
                batch.append(
                    {
                        "bucket": r["bucket"],
                        "tag_id": r["tag_id"],
                        "avg_value": float(r["avg_value"]),
                        "min_value": float(r["min_value"]),
                        "max_value": float(r["max_value"]),
                        "stddev_value": (
                            float(r["stddev_value"]) if r["stddev_value"] is not None else None
                        ),
                        "max_quality": int(r["max_quality"]),
                        "sample_count": int(r["sample_count"]),
                    }
                )
                if len(batch) >= batch_size:
                    yield batch
                    batch = []
            if batch:
                yield batch

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
                "INSERT INTO tags "
                "(tag_id, name, modbus_host, modbus_port, unit_id, "
                "register_address, register_type, byte_order, "
                'gain, "offset", unit, polling_interval_ms, polling_preset, '
                "status, stuck_at_preset, stuck_at_seconds, "
                "rate_of_change_threshold, spc_enabled) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, "
                "$12, $13, $14, $15, $16, $17, $18) "
                "RETURNING *",
                tag.tag_id,
                tag.name,
                tag.modbus_host,
                tag.modbus_port,
                tag.unit_id,
                tag.register_address,
                tag.register_type,
                tag.byte_order,
                tag.gain,
                tag.offset,
                tag.unit,
                tag.polling_interval_ms,
                tag.polling_preset,
                tag.status,
                tag.stuck_at_preset,
                tag.stuck_at_seconds,
                tag.rate_of_change_threshold,
                tag.spc_enabled,
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
                profile.name,
                profile.host,
                profile.port,
                profile.unit_id_start,
                profile.unit_id_end,
                profile.status,
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

    async def upsert_asset_template(self, template: AssetTemplate) -> AssetTemplate:
        """Asset template'i slug bazında upsert eder.

        Template satırı slug UNIQUE üzerinden, roller (template_id, role_key)
        bazında, KPI'lar (template_id, name) bazında upsert edilir. Orphan
        roller (YAML'da olmayan ama DB'de kalan) bırakılır — tag_bindings
        CASCADE davranışı nedeniyle silme yapılmaz. Tek transaction.
        """
        pool = self._get_pool()
        async with pool.acquire() as conn, conn.transaction():
            tmpl_row = await conn.fetchrow(
                """
                INSERT INTO asset_templates (slug, name, description, icon)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (slug) DO UPDATE SET
                    name = EXCLUDED.name,
                    description = EXCLUDED.description,
                    icon = EXCLUDED.icon
                RETURNING *
                """,
                template.slug,
                template.name,
                template.description,
                template.icon,
            )
            assert tmpl_row is not None
            tmpl_id = int(tmpl_row["id"])

            role_rows: list[TemplateRole] = []
            for role in template.roles:
                role_row = await conn.fetchrow(
                    """
                    INSERT INTO template_roles
                        (template_id, role_key, label, unit_hint, required, sort_order)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    ON CONFLICT (template_id, role_key) DO UPDATE SET
                        label = EXCLUDED.label,
                        unit_hint = EXCLUDED.unit_hint,
                        required = EXCLUDED.required,
                        sort_order = EXCLUDED.sort_order
                    RETURNING *
                    """,
                    tmpl_id,
                    role.role_key,
                    role.label,
                    role.unit_hint,
                    role.required,
                    role.sort_order,
                )
                assert role_row is not None
                role_rows.append(_row_to_template_role(role_row))

            kpi_rows: list[KpiDefinition] = []
            for kpi in template.kpi_definitions:
                kpi_row = await conn.fetchrow(
                    """
                    INSERT INTO kpi_definitions
                        (template_id, name, formula, unit, description)
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT (template_id, name) DO UPDATE SET
                        formula = EXCLUDED.formula,
                        unit = EXCLUDED.unit,
                        description = EXCLUDED.description
                    RETURNING *
                    """,
                    tmpl_id,
                    kpi.name,
                    kpi.formula,
                    kpi.unit,
                    kpi.description,
                )
                assert kpi_row is not None
                kpi_rows.append(_row_to_kpi_definition(kpi_row))

        result = _row_to_asset_template(tmpl_row)
        result.roles = role_rows
        result.kpi_definitions = kpi_rows
        return result

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
                instance.template_id,
                instance.name,
                instance.description,
                instance.location,
                instance.status,
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
            f"UPDATE asset_instances SET {', '.join(set_parts)} WHERE id = ${idx_where} RETURNING *"
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

    async def list_active_maintenance_instances(
        self,
        now: datetime,
    ) -> list[AssetInstance]:
        """Aktif bakım modunda olan instance'ları döndürür (P-04)."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM asset_instances "
                "WHERE maintenance_started_at IS NOT NULL "
                "  AND (maintenance_mode_until IS NULL "
                "       OR maintenance_mode_until > $1) "
                "ORDER BY id",
                now,
            )
        return [_row_to_asset_instance(row) for row in rows]

    async def list_expired_maintenance_instances(
        self,
        now: datetime,
    ) -> list[AssetInstance]:
        """Süresi dolmuş bakım modu kayıtlarını döndürür (P-04)."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM asset_instances "
                "WHERE maintenance_mode_until IS NOT NULL "
                "  AND maintenance_mode_until <= $1 "
                "ORDER BY id",
                now,
            )
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
                binding.instance_id,
                binding.role_id,
                binding.tag_id,
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

    async def list_tag_bindings_all(self) -> list[TagBinding]:
        """Tüm binding'leri tek query'de döndürür (P-04 maintenance cache)."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM tag_bindings ORDER BY id",
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
                        instance_id,
                        b.role_id,
                        b.tag_id,
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
                threshold.tag_id,
                threshold.name,
                threshold.direction,
                threshold.set_point,
                threshold.severity,
                threshold.debounce_seconds,
                threshold.hysteresis,
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

        sql = f"UPDATE thresholds SET {', '.join(set_parts)} WHERE id = ${idx_where} RETURNING *"

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
        """Yeni alarm event kaydı oluşturur ve döndürür.

        ``escalated_from`` / ``escalated_at`` kolonlarına insert sırasında
        dokunulmaz — default NULL. Yükseltme ``escalation_loop`` tarafından
        ``update_alarm_event`` üzerinden yapılır (R-06).
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO alarm_events "
                "(threshold_id, tag_id, state, triggered_at, "
                "acknowledged_at, cleared_at, trigger_value, clear_value, "
                "notes, is_test, source, severity, message) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, "
                "$11, $12, $13) "
                "RETURNING *",
                event.threshold_id,
                event.tag_id,
                event.state,
                event.triggered_at,
                event.acknowledged_at,
                event.cleared_at,
                event.trigger_value,
                event.clear_value,
                event.notes,
                event.is_test,
                event.source,
                event.severity,
                event.message,
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

        sql = f"UPDATE alarm_events SET {', '.join(set_parts)} WHERE id = ${idx_where} RETURNING *"

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
                f"{_ALARM_EVENT_LABEL_JOIN_SELECT} WHERE a.id = $1",
                event_id,
            )
        if row is None:
            return None
        return _row_to_alarm_event_with_label(row)

    async def list_alarm_events(
        self,
        state: str | None = None,
        tag_id: str | None = None,
        limit: int = 100,
        is_test: bool | None = None,
        source: str | None = None,
    ) -> list[AlarmEvent]:
        """Alarm event listesini döndürür."""
        pool = self._get_pool()
        conditions: list[str] = []
        params: list[object] = []
        idx = 1

        if state is not None:
            conditions.append(f"a.state = ${idx}")
            params.append(state)
            idx += 1

        if tag_id is not None:
            conditions.append(f"a.tag_id = ${idx}")
            params.append(tag_id)
            idx += 1

        if is_test is not None:
            conditions.append(f"a.is_test = ${idx}")
            params.append(is_test)
            idx += 1

        if source is not None:
            conditions.append(f"a.source = ${idx}")
            params.append(source)
            idx += 1

        where_clause = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(limit)
        sql = (
            f"{_ALARM_EVENT_LABEL_JOIN_SELECT}{where_clause} "
            f"ORDER BY a.triggered_at DESC LIMIT ${idx}"
        )

        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
        return [_row_to_alarm_event_with_label(row) for row in rows]

    async def get_active_alarm_for_threshold(
        self,
        threshold_id: int,
    ) -> AlarmEvent | None:
        """Threshold için aktif (cleared olmayan) en son alarm döndürür."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                f"{_ALARM_EVENT_LABEL_JOIN_SELECT} "
                "WHERE a.threshold_id = $1 AND a.state != 'cleared' "
                "ORDER BY a.triggered_at DESC LIMIT 1",
                threshold_id,
            )
        if row is None:
            return None
        return _row_to_alarm_event_with_label(row)

    # --- Alarm Event Label implementasyonları (R-05 / V11-301) ---

    async def upsert_alarm_label(
        self,
        alarm_event_id: int,
        label_class: LabelClass,
        labeled_by_user_id: int,
        notes: str = "",
    ) -> AlarmEventLabel:
        """Alarmı etiketler; alarm_event_id çakışırsa mevcut satırı günceller."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO alarm_event_labels "
                "(alarm_event_id, label_class, labeled_by_user_id, notes) "
                "VALUES ($1, $2, $3, $4) "
                "ON CONFLICT (alarm_event_id) DO UPDATE SET "
                "  label_class = EXCLUDED.label_class, "
                "  labeled_by_user_id = EXCLUDED.labeled_by_user_id, "
                "  labeled_at = NOW(), "
                "  notes = EXCLUDED.notes "
                "RETURNING *",
                alarm_event_id,
                label_class,
                labeled_by_user_id,
                notes,
            )
        assert row is not None
        return _row_to_alarm_event_label(row)

    async def get_alarm_label(self, alarm_event_id: int) -> AlarmEventLabel | None:
        """Alarmın etiketini döndürür."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM alarm_event_labels WHERE alarm_event_id = $1",
                alarm_event_id,
            )
        if row is None:
            return None
        return _row_to_alarm_event_label(row)

    async def list_unlabeled_alarms(
        self,
        limit: int = 100,
    ) -> list[AlarmEvent]:
        """Etiketlenmemiş alarmları döndürür (review queue için)."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                f"{_ALARM_EVENT_LABEL_JOIN_SELECT} "
                "WHERE l.id IS NULL AND a.is_test = FALSE "
                "ORDER BY a.triggered_at DESC LIMIT $1",
                limit,
            )
        # WHERE l.id IS NULL filtresi sayesinde label_id hep NULL → label hep None,
        # ama tek SELECT projeksiyonunu ortak helper ile çözüyoruz (tutarlılık).
        return [_row_to_alarm_event_with_label(row) for row in rows]

    async def count_labels_by_class(
        self,
        since: datetime | None = None,
    ) -> dict[str, int]:
        """4 sınıf için etiket sayımlarını döndürür (eksik sınıfa 0)."""
        pool = self._get_pool()
        if since is None:
            sql = (
                "SELECT label_class, COUNT(*) AS cnt "
                "FROM alarm_event_labels GROUP BY label_class"
            )
            params: tuple[object, ...] = ()
        else:
            sql = (
                "SELECT label_class, COUNT(*) AS cnt "
                "FROM alarm_event_labels WHERE labeled_at >= $1 "
                "GROUP BY label_class"
            )
            params = (since,)

        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)

        counts: dict[str, int] = dict.fromkeys(LABEL_CLASS_VALUES, 0)
        for row in rows:
            counts[row["label_class"]] = int(row["cnt"])
        return counts

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
                entry.category,
                entry.action,
                entry.entity_type,
                entry.entity_id,
                entry.detail,
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
                result.instance_id,
                result.kpi_definition_id,
                result.bucket_start,
                result.value,
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
                [(r.instance_id, r.kpi_definition_id, r.bucket_start, r.value) for r in results],
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
        sql = f"SELECT * FROM kpi_results {where} ORDER BY bucket_start DESC LIMIT ${idx}"

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
        return {row["kpi_definition_id"]: _row_to_kpi_result(row) for row in rows}

    # --- Anomaly Scores implementasyonları ---

    async def insert_anomaly_score(self, score: AnomalyScore) -> AnomalyScore:
        """Yeni anomali skoru kaydeder ve döndürür.

        Migration 040 ile ``engine_type`` kolonu eklendi — IF skorlari
        'if', autoencoder skorlari 'ae' degerini alir. INSERT'te explicit
        verilir (DB default 'if' fallback'i de korunur).
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO anomaly_scores "
                "(instance_id, timestamp, score, is_anomaly, feature_vector, engine_type) "
                "VALUES ($1, $2, $3, $4, $5, $6) "
                "RETURNING *",
                score.instance_id,
                score.timestamp,
                score.score,
                score.is_anomaly,
                score.feature_vector,
                score.engine_type,
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
        sql = "SELECT * FROM anomaly_scores WHERE instance_id = $1 ORDER BY timestamp DESC LIMIT $2"
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, instance_id, limit)
        return [_row_to_anomaly_score(row) for row in rows]

    async def get_latest_anomaly_score(
        self,
        instance_id: int,
    ) -> AnomalyScore | None:
        """En son anomali skorunu döndürür."""
        pool = self._get_pool()
        sql = "SELECT * FROM anomaly_scores WHERE instance_id = $1 ORDER BY timestamp DESC LIMIT 1"
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
            sql = "SELECT COUNT(*) FROM anomaly_scores WHERE is_anomaly = TRUE AND timestamp >= $1"
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
        """Push subscription kaydeder veya günceller (endpoint bazlı upsert).

        Çakışma davranışı (P-03): aynı ``endpoint`` ikinci kez subscribe
        edilirse cihaz anahtarları (``p256dh``, ``auth``) ve ``label`` /
        ``created_by_user_id`` güncellenir; mevcut ``enabled`` ve severity
        tier ayarları korunur (kullanıcının önceki seçimi sıfırlanmasın).
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO push_subscriptions "
                "(endpoint, p256dh, auth, notify_warn, notify_crit, "
                "notify_info, notify_emergency, "
                "quiet_start, quiet_end, label, enabled, "
                "created_by_user_id) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12) "
                "ON CONFLICT (endpoint) DO UPDATE SET "
                "p256dh = EXCLUDED.p256dh, auth = EXCLUDED.auth, "
                "label = EXCLUDED.label, "
                "created_by_user_id = EXCLUDED.created_by_user_id, "
                "updated_at = NOW() "
                "RETURNING *",
                sub.endpoint,
                sub.p256dh,
                sub.auth,
                sub.notify_warn,
                sub.notify_crit,
                sub.notify_info,
                sub.notify_emergency,
                sub.quiet_start,
                sub.quiet_end,
                sub.label,
                sub.enabled,
                sub.created_by_user_id,
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
            rows = await conn.fetch("SELECT * FROM push_subscriptions ORDER BY created_at DESC")
        return [_row_to_push_subscription(row) for row in rows]

    async def get_push_subscription_by_endpoint(
        self,
        endpoint: str,
    ) -> PushSubscription | None:
        """Endpoint ile tek aboneliği döndürür. Bulunamazsa None."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM push_subscriptions WHERE endpoint = $1",
                endpoint,
            )
        if row is None:
            return None
        return _row_to_push_subscription(row)

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

    # --- Overview Charts (dinamik slot) implementasyonlari ---

    async def list_overview_charts(self) -> list[OverviewChart]:
        """Tum chart slotlarini sort_order + created_at sirasiyla dondurur."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM overview_charts ORDER BY sort_order, created_at",
            )
        return [_row_to_overview_chart(r) for r in rows]

    async def get_overview_chart(self, chart_key: str) -> OverviewChart | None:
        """Tek bir chart slotunu dondurur. Yoksa None."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM overview_charts WHERE chart_key = $1",
                chart_key,
            )
        if row is None:
            return None
        return _row_to_overview_chart(row)

    async def insert_overview_chart(self, chart: OverviewChart) -> OverviewChart:
        """Yeni chart slotu ekler."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO overview_charts (chart_key, title, sort_order) "
                "VALUES ($1, $2, $3) RETURNING *",
                chart.chart_key,
                chart.title,
                chart.sort_order,
            )
        assert row is not None
        return _row_to_overview_chart(row)

    async def update_overview_chart(
        self,
        chart_key: str,
        updates: dict[str, object],
    ) -> OverviewChart | None:
        """Chart slotu alanlarini gunceller; bilinmeyen alan varsa ValueError."""
        invalid = set(updates.keys()) - _ALLOWED_OVERVIEW_CHART_UPDATE_FIELDS
        if invalid:
            msg = f"Guncellenemeyen alanlar: {invalid}"
            raise ValueError(msg)
        if not updates:
            return await self.get_overview_chart(chart_key)

        set_parts: list[str] = []
        values: list[object] = []
        for i, (col, val) in enumerate(updates.items(), start=1):
            set_parts.append(f"{col} = ${i}")
            values.append(val)
        idx = len(values) + 1
        set_clause = ", ".join(set_parts)
        values.append(chart_key)

        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                f"UPDATE overview_charts SET {set_clause} WHERE chart_key = ${idx} RETURNING *",
                *values,
            )
        if row is None:
            return None
        return _row_to_overview_chart(row)

    async def delete_overview_chart(self, chart_key: str) -> bool:
        """Chart slotunu siler. Tag bindingleri FK CASCADE ile duser."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM overview_charts WHERE chart_key = $1",
                chart_key,
            )
        return bool(result.endswith(" 1"))

    # --- Overview Chart Tags implementasyonlari ---

    async def list_overview_chart_tags(
        self,
        chart_key: str | None = None,
    ) -> list[OverviewChartTag]:
        """Overview grafik tag konfigurasyonunu dondurur."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            if chart_key is not None:
                rows = await conn.fetch(
                    "SELECT * FROM overview_chart_tags WHERE chart_key = $1 ORDER BY sort_order",
                    chart_key,
                )
            else:
                rows = await conn.fetch(
                    "SELECT * FROM overview_chart_tags ORDER BY chart_key, sort_order",
                )
        return [_row_to_overview_chart_tag(row) for row in rows]

    async def replace_overview_chart_tags(
        self,
        chart_key: str,
        tag_ids: list[str],
    ) -> list[OverviewChartTag]:
        """Bir grafik slotunun tag listesini yenisiyle degistirir (tek transaction)."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "DELETE FROM overview_chart_tags WHERE chart_key = $1",
                    chart_key,
                )
                result: list[OverviewChartTag] = []
                for idx, tid in enumerate(tag_ids):
                    row = await conn.fetchrow(
                        "INSERT INTO overview_chart_tags (chart_key, tag_id, sort_order) "
                        "VALUES ($1, $2, $3) RETURNING *",
                        chart_key,
                        tid,
                        idx,
                    )
                    assert row is not None
                    result.append(_row_to_overview_chart_tag(row))
                return result

    # --- Maintenance Checklist CRUD ---

    async def insert_maintenance_checklist(
        self,
        checklist: MaintenanceChecklist,
    ) -> MaintenanceChecklist:
        """Checklist + steps'i tek transaction ile oluşturur."""
        pool = self._get_pool()
        async with pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                "INSERT INTO maintenance_checklists "
                "(slug, title, description, category, asset_template_id) "
                "VALUES ($1, $2, $3, $4, $5) RETURNING *",
                checklist.slug,
                checklist.title,
                checklist.description,
                checklist.category,
                checklist.asset_template_id,
            )
            assert row is not None
            new_checklist = _row_to_maintenance_checklist(row)
            new_steps: list[MaintenanceChecklistStep] = []
            for idx, step in enumerate(checklist.steps):
                srow = await conn.fetchrow(
                    "INSERT INTO maintenance_checklist_steps "
                    "(checklist_id, sort_order, text, estimated_minutes) "
                    "VALUES ($1, $2, $3, $4) RETURNING *",
                    new_checklist.id,
                    idx,
                    step.text,
                    step.estimated_minutes,
                )
                assert srow is not None
                new_steps.append(_row_to_maintenance_step(srow))
            new_checklist.steps = new_steps
            return new_checklist

    async def update_maintenance_checklist(
        self,
        checklist_id: int,
        updates: dict[str, object],
    ) -> MaintenanceChecklist | None:
        """Checklist alanlarını günceller (steps ayrı replace ile)."""
        invalid = set(updates.keys()) - _ALLOWED_CHECKLIST_UPDATE_FIELDS
        if invalid:
            msg = f"Güncellenemeyen alanlar: {invalid}"
            raise ValueError(msg)
        if not updates:
            return await self.get_maintenance_checklist(checklist_id)
        set_parts: list[str] = []
        values: list[object] = []
        for i, (col, val) in enumerate(updates.items(), start=1):
            set_parts.append(f"{col} = ${i}")
            values.append(val)
        idx = len(values) + 1
        set_clause = ", ".join(set_parts)
        values.append(checklist_id)
        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                f"UPDATE maintenance_checklists SET {set_clause}, "
                f"updated_at = NOW() WHERE id = ${idx} RETURNING *",
                *values,
            )
        if row is None:
            return None
        return await self.get_maintenance_checklist(checklist_id)

    async def delete_maintenance_checklist(self, checklist_id: int) -> bool:
        """Checklist'i siler (steps CASCADE). Başarılıysa True."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            status = await conn.execute(
                "DELETE FROM maintenance_checklists WHERE id = $1",
                checklist_id,
            )
        return str(status) == "DELETE 1"

    async def get_maintenance_checklist(
        self,
        checklist_id: int,
    ) -> MaintenanceChecklist | None:
        """Tekil checklist + steps."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM maintenance_checklists WHERE id = $1",
                checklist_id,
            )
            if row is None:
                return None
            checklist = _row_to_maintenance_checklist(row)
            step_rows = await conn.fetch(
                "SELECT * FROM maintenance_checklist_steps "
                "WHERE checklist_id = $1 ORDER BY sort_order, id",
                checklist_id,
            )
            checklist.steps = [_row_to_maintenance_step(r) for r in step_rows]
            return checklist

    async def list_maintenance_checklists(
        self,
        category: str | None = None,
    ) -> list[MaintenanceChecklist]:
        """Checklist listesi (steps dahil, tek SQL round-trip ile)."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            if category is None:
                rows = await conn.fetch(
                    "SELECT * FROM maintenance_checklists ORDER BY title",
                )
            else:
                rows = await conn.fetch(
                    "SELECT * FROM maintenance_checklists WHERE category = $1 ORDER BY title",
                    category,
                )
            checklists = [_row_to_maintenance_checklist(r) for r in rows]
            if not checklists:
                return checklists
            ids = [c.id for c in checklists if c.id is not None]
            step_rows = await conn.fetch(
                "SELECT * FROM maintenance_checklist_steps "
                "WHERE checklist_id = ANY($1::int[]) "
                "ORDER BY checklist_id, sort_order, id",
                ids,
            )
            steps_by_cid: dict[int, list[MaintenanceChecklistStep]] = {}
            for sr in step_rows:
                steps_by_cid.setdefault(sr["checklist_id"], []).append(
                    _row_to_maintenance_step(sr),
                )
            for c in checklists:
                c.steps = steps_by_cid.get(c.id or 0, [])
            return checklists

    async def replace_maintenance_checklist_steps(
        self,
        checklist_id: int,
        steps: list[MaintenanceChecklistStep],
    ) -> list[MaintenanceChecklistStep]:
        """Tüm adımları atomik olarak yenileriyle değiştirir."""
        pool = self._get_pool()
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "DELETE FROM maintenance_checklist_steps WHERE checklist_id = $1",
                checklist_id,
            )
            result: list[MaintenanceChecklistStep] = []
            for idx, st in enumerate(steps):
                row = await conn.fetchrow(
                    "INSERT INTO maintenance_checklist_steps "
                    "(checklist_id, sort_order, text, estimated_minutes) "
                    "VALUES ($1, $2, $3, $4) RETURNING *",
                    checklist_id,
                    idx,
                    st.text,
                    st.estimated_minutes,
                )
                assert row is not None
                result.append(_row_to_maintenance_step(row))
            return result

    # --- Maintenance Schedule CRUD ---

    async def insert_maintenance_schedule(
        self,
        schedule: MaintenanceSchedule,
    ) -> MaintenanceSchedule:
        """Yeni periyodik bakım takvimi kaydı."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO maintenance_schedules "
                "(checklist_id, asset_template_id, asset_instance_id, "
                " period_kind, period_value, anchor_date, next_due_at, "
                " notify_lead_hours, enabled) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9) RETURNING *",
                schedule.checklist_id,
                schedule.asset_template_id,
                schedule.asset_instance_id,
                schedule.period_kind,
                schedule.period_value,
                schedule.anchor_date,
                schedule.next_due_at,
                schedule.notify_lead_hours,
                schedule.enabled,
            )
        assert row is not None
        return _row_to_maintenance_schedule(row)

    async def update_maintenance_schedule(
        self,
        schedule_id: int,
        updates: dict[str, object],
    ) -> MaintenanceSchedule | None:
        """Schedule alanlarını günceller."""
        invalid = set(updates.keys()) - _ALLOWED_SCHEDULE_UPDATE_FIELDS
        if invalid:
            msg = f"Güncellenemeyen alanlar: {invalid}"
            raise ValueError(msg)
        if not updates:
            return await self.get_maintenance_schedule(schedule_id)
        set_parts: list[str] = []
        values: list[object] = []
        for i, (col, val) in enumerate(updates.items(), start=1):
            set_parts.append(f"{col} = ${i}")
            values.append(val)
        idx = len(values) + 1
        set_clause = ", ".join(set_parts)
        values.append(schedule_id)
        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                f"UPDATE maintenance_schedules SET {set_clause}, "
                f"updated_at = NOW() WHERE id = ${idx} RETURNING *",
                *values,
            )
        if row is None:
            return None
        return _row_to_maintenance_schedule(row)

    async def delete_maintenance_schedule(self, schedule_id: int) -> bool:
        """Schedule'ı siler."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            status = await conn.execute(
                "DELETE FROM maintenance_schedules WHERE id = $1",
                schedule_id,
            )
        return str(status) == "DELETE 1"

    async def get_maintenance_schedule(
        self,
        schedule_id: int,
    ) -> MaintenanceSchedule | None:
        """Tekil schedule."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM maintenance_schedules WHERE id = $1",
                schedule_id,
            )
        if row is None:
            return None
        return _row_to_maintenance_schedule(row)

    async def list_maintenance_schedules(
        self,
        enabled: bool | None = None,
    ) -> list[MaintenanceSchedule]:
        """Schedule listesi."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            if enabled is None:
                rows = await conn.fetch(
                    "SELECT * FROM maintenance_schedules ORDER BY next_due_at",
                )
            else:
                rows = await conn.fetch(
                    "SELECT * FROM maintenance_schedules WHERE enabled = $1 ORDER BY next_due_at",
                    enabled,
                )
        return [_row_to_maintenance_schedule(r) for r in rows]

    async def list_due_maintenance_schedules(
        self,
        now: datetime,
    ) -> list[MaintenanceSchedule]:
        """Scheduler için — vadesi gelmiş aktif schedule'lar."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM maintenance_schedules "
                "WHERE enabled = TRUE AND next_due_at <= $1 "
                "ORDER BY next_due_at",
                now,
            )
        return [_row_to_maintenance_schedule(r) for r in rows]

    # --- Maintenance Task CRUD ---

    async def insert_maintenance_task(
        self,
        task: MaintenanceTask,
    ) -> MaintenanceTask:
        """Yeni maintenance task kaydı."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO maintenance_tasks "
                "(schedule_id, checklist_id, asset_instance_id, source, "
                " alarm_event_id, title_snapshot, due_at, started_at, "
                " completed_at, completed_by, notes, status) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12) "
                "RETURNING *",
                task.schedule_id,
                task.checklist_id,
                task.asset_instance_id,
                task.source,
                task.alarm_event_id,
                task.title_snapshot,
                task.due_at,
                task.started_at,
                task.completed_at,
                task.completed_by,
                task.notes,
                task.status,
            )
        assert row is not None
        return _row_to_maintenance_task(row)

    async def update_maintenance_task(
        self,
        task_id: int,
        updates: dict[str, object],
    ) -> MaintenanceTask | None:
        """Task alanlarını günceller."""
        invalid = set(updates.keys()) - _ALLOWED_TASK_UPDATE_FIELDS
        if invalid:
            msg = f"Güncellenemeyen alanlar: {invalid}"
            raise ValueError(msg)
        if not updates:
            return await self.get_maintenance_task(task_id)
        set_parts: list[str] = []
        values: list[object] = []
        for i, (col, val) in enumerate(updates.items(), start=1):
            set_parts.append(f"{col} = ${i}")
            values.append(val)
        idx = len(values) + 1
        set_clause = ", ".join(set_parts)
        values.append(task_id)
        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                f"UPDATE maintenance_tasks SET {set_clause} WHERE id = ${idx} RETURNING *",
                *values,
            )
        if row is None:
            return None
        return _row_to_maintenance_task(row)

    async def get_maintenance_task(
        self,
        task_id: int,
    ) -> MaintenanceTask | None:
        """Tekil task."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM maintenance_tasks WHERE id = $1",
                task_id,
            )
        if row is None:
            return None
        return _row_to_maintenance_task(row)

    async def list_upcoming_maintenance_tasks(
        self,
        within_hours: int = 48,
    ) -> list[MaintenanceTask]:
        """Yaklaşan pending/in_progress task'lar (Overview widget)."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM maintenance_tasks "
                "WHERE status IN ('pending', 'in_progress') "
                "AND due_at IS NOT NULL "
                "AND due_at <= NOW() + make_interval(hours => $1) "
                "ORDER BY due_at",
                within_hours,
            )
        return [_row_to_maintenance_task(r) for r in rows]

    async def list_recent_maintenance_tasks(
        self,
        limit: int = 50,
    ) -> list[MaintenanceTask]:
        """Son tamamlanmış / atlanmış / missed task'lar."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM maintenance_tasks "
                "WHERE completed_at IS NOT NULL "
                "OR status IN ('skipped', 'missed') "
                "ORDER BY COALESCE(completed_at, created_at) DESC "
                "LIMIT $1",
                limit,
            )
        return [_row_to_maintenance_task(r) for r in rows]

    async def list_maintenance_tasks_for_schedule(
        self,
        schedule_id: int,
    ) -> list[MaintenanceTask]:
        """Bir schedule'a ait tüm task'lar."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM maintenance_tasks WHERE schedule_id = $1 "
                "ORDER BY due_at DESC NULLS LAST",
                schedule_id,
            )
        return [_row_to_maintenance_task(r) for r in rows]

    # --- Maintenance Task Step Result ---

    async def upsert_maintenance_task_step_result(
        self,
        result: MaintenanceTaskStepResult,
    ) -> MaintenanceTaskStepResult:
        """Task + step unique constraint ile upsert."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO maintenance_task_step_results "
                "(task_id, step_id, checked, note, completed_at) "
                "VALUES ($1, $2, $3, $4, $5) "
                "ON CONFLICT (task_id, step_id) DO UPDATE SET "
                "  checked = EXCLUDED.checked, "
                "  note = EXCLUDED.note, "
                "  completed_at = EXCLUDED.completed_at "
                "RETURNING *",
                result.task_id,
                result.step_id,
                result.checked,
                result.note,
                result.completed_at,
            )
        assert row is not None
        return _row_to_maintenance_step_result(row)

    async def list_maintenance_task_step_results(
        self,
        task_id: int,
    ) -> list[MaintenanceTaskStepResult]:
        """Task'ın tüm adım sonuçları (step sort_order sırasında)."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT r.* FROM maintenance_task_step_results r "
                "JOIN maintenance_checklist_steps s ON r.step_id = s.id "
                "WHERE r.task_id = $1 ORDER BY s.sort_order, s.id",
                task_id,
            )
        return [_row_to_maintenance_step_result(r) for r in rows]

    # --- Alarm Checklist Mapping ---

    async def upsert_alarm_checklist_mapping(
        self,
        threshold_id: int,
        checklist_id: int,
    ) -> AlarmChecklistMapping:
        """Threshold → checklist eşlemesi (1:1)."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO alarm_checklist_mappings "
                "(threshold_id, checklist_id) VALUES ($1, $2) "
                "ON CONFLICT (threshold_id) DO UPDATE SET "
                "  checklist_id = EXCLUDED.checklist_id "
                "RETURNING *",
                threshold_id,
                checklist_id,
            )
        assert row is not None
        return _row_to_alarm_checklist_mapping(row)

    async def delete_alarm_checklist_mapping(
        self,
        threshold_id: int,
    ) -> bool:
        """Threshold eşlemesini kaldırır."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            status = await conn.execute(
                "DELETE FROM alarm_checklist_mappings WHERE threshold_id = $1",
                threshold_id,
            )
        return str(status) == "DELETE 1"

    async def get_alarm_checklist_mapping(
        self,
        threshold_id: int,
    ) -> AlarmChecklistMapping | None:
        """Bir threshold'un eşlemesi (varsa)."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM alarm_checklist_mappings WHERE threshold_id = $1",
                threshold_id,
            )
        if row is None:
            return None
        return _row_to_alarm_checklist_mapping(row)

    async def list_alarm_checklist_mappings(
        self,
    ) -> list[AlarmChecklistMapping]:
        """Tüm alarm → checklist eşlemeleri."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM alarm_checklist_mappings ORDER BY threshold_id",
            )
        return [_row_to_alarm_checklist_mapping(r) for r in rows]

    async def count_alarm_events_for_threshold(
        self,
        threshold_id: int,
        since: datetime,
    ) -> int:
        """Verilen zaman sonrası threshold'un tetiklenme sayısı."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT COUNT(*) AS cnt FROM alarm_events "
                "WHERE threshold_id = $1 AND triggered_at >= $2",
                threshold_id,
                since,
            )
        assert row is not None
        return int(row["cnt"])

    # --- Retention Config (F11 Paket F) ---

    async def get_retention_config(self) -> RetentionConfig:
        """Singleton retention ayarlarını döndürür (id=1)."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT raw_retention_days, auto_clean_enabled, "
                "       push_global_enabled, updated_at, updated_by, "
                "       global_maintenance_until, global_maintenance_reason, "
                "       global_maintenance_started_by_user_id, "
                "       global_maintenance_started_at, "
                "       resource_cpu_warn_pct, resource_ram_warn_pct, "
                "       ml_inference_enabled, "
                "       escalation_warn_to_crit_minutes "
                "FROM retention_config WHERE id = 1",
            )
        # Migration 026 INSERT garantiler ki satır her zaman var; defansif
        # fallback yine de ekleniyor — downgrade/upgrade sırasında
        # migration tamamlanmadan connect edilirse diye.
        if row is None:
            msg = "retention_config singleton satırı eksik — migration 026 uygulandı mı?"
            raise RuntimeError(msg)
        return RetentionConfig(
            raw_retention_days=int(row["raw_retention_days"]),
            auto_clean_enabled=bool(row["auto_clean_enabled"]),
            push_global_enabled=bool(row["push_global_enabled"]),
            updated_at=row["updated_at"],
            updated_by=row["updated_by"],
            global_maintenance_until=row["global_maintenance_until"],
            global_maintenance_reason=row["global_maintenance_reason"],
            global_maintenance_started_by_user_id=row[
                "global_maintenance_started_by_user_id"
            ],
            global_maintenance_started_at=row["global_maintenance_started_at"],
            resource_cpu_warn_pct=int(row["resource_cpu_warn_pct"]),
            resource_ram_warn_pct=int(row["resource_ram_warn_pct"]),
            ml_inference_enabled=bool(row["ml_inference_enabled"]),
            escalation_warn_to_crit_minutes=int(
                row["escalation_warn_to_crit_minutes"],
            ),
        )

    async def update_retention_config(
        self,
        raw_retention_days: int | None = None,
        auto_clean_enabled: bool | None = None,
        updated_by: str = "user",
        push_global_enabled: bool | None = None,
        resource_cpu_warn_pct: int | None = None,
        resource_ram_warn_pct: int | None = None,
        ml_inference_enabled: bool | None = None,
        escalation_warn_to_crit_minutes: int | None = None,
    ) -> RetentionConfig:
        """retention_config satırını ve TimescaleDB policy'yi senkron günceller.

        Akış:
            1. Mevcut satırı oku.
            2. Yeni değerleri uygula (None olmayanlar).
            3. Tek transaction içinde: satırı güncelle + policy'yi senkronla.

        Policy senkronu:
            auto_clean_enabled=False → tag_readings + features retention policy
                remove_retention_policy ile kaldırılır (idempotent).
            auto_clean_enabled=True  → önce remove, sonra add (yeni aralıkla).

        ``push_global_enabled`` (P-03 master switch) sadece satıra yazılır;
        runtime'da push_sender erken-dönüşle okur (TimescaleDB policy ile
        ilgisiz).

        ``resource_cpu_warn_pct`` / ``resource_ram_warn_pct`` (V11-111 / P-06):
        ResourceMonitor tarafından tick'te okunur; CHECK constraint 50-99
        aralık doğrulamasını DB tarafında yapar.

        ``ml_inference_enabled`` (R-04 / Migration 034): Sistem-geneli ML
        master switch. AnomalyDetector tick'te erken-dönüşle okur; push
        master switch ile aynı desen.

        ``escalation_warn_to_crit_minutes`` (R-06 / Migration 036):
        ``escalation_loop`` warn alarm'ı bu kadar dakika sonra crit'e
        yükseltir. CHECK constraint 5-240 (DB-side).

        ``add_retention_policy`` transaction içinde çalışmaya uygun — background
        worker job kaydı oluşturur; TimescaleDB docs'ına göre güvenli.
        """
        if raw_retention_days is not None and raw_retention_days <= 0:
            msg = f"raw_retention_days pozitif olmalı, alınan: {raw_retention_days}"
            raise ValueError(msg)
        if escalation_warn_to_crit_minutes is not None and not (
            5 <= escalation_warn_to_crit_minutes <= 240
        ):
            msg = (
                "escalation_warn_to_crit_minutes 5-240 aralığında olmalı, "
                f"alınan: {escalation_warn_to_crit_minutes}"
            )
            raise ValueError(msg)

        pool = self._get_pool()
        async with pool.acquire() as conn, conn.transaction():
            # Satırı güncelle — parametreler yoksa mevcut değerler korunur.
            # Global maintenance kolonları burada dokunulmaz; ayrı metot
            # (``update_global_maintenance``) ile yönetilir.
            row = await conn.fetchrow(
                "UPDATE retention_config SET "
                "    raw_retention_days = COALESCE($1, raw_retention_days), "
                "    auto_clean_enabled = COALESCE($2, auto_clean_enabled), "
                "    push_global_enabled = COALESCE($4, push_global_enabled), "
                "    resource_cpu_warn_pct = COALESCE($5, resource_cpu_warn_pct), "
                "    resource_ram_warn_pct = COALESCE($6, resource_ram_warn_pct), "
                "    ml_inference_enabled = COALESCE($7, ml_inference_enabled), "
                "    escalation_warn_to_crit_minutes = "
                "        COALESCE($8, escalation_warn_to_crit_minutes), "
                "    updated_by = $3, "
                "    updated_at = NOW() "
                "WHERE id = 1 "
                "RETURNING raw_retention_days, auto_clean_enabled, "
                "          push_global_enabled, updated_at, updated_by, "
                "          global_maintenance_until, global_maintenance_reason, "
                "          global_maintenance_started_by_user_id, "
                "          global_maintenance_started_at, "
                "          resource_cpu_warn_pct, resource_ram_warn_pct, "
                "          ml_inference_enabled, "
                "          escalation_warn_to_crit_minutes",
                raw_retention_days,
                auto_clean_enabled,
                updated_by,
                push_global_enabled,
                resource_cpu_warn_pct,
                resource_ram_warn_pct,
                ml_inference_enabled,
                escalation_warn_to_crit_minutes,
            )
            assert row is not None, "retention_config satırı güncellenemedi"
            new_days = int(row["raw_retention_days"])
            new_auto = bool(row["auto_clean_enabled"])
            new_push_global = bool(row["push_global_enabled"])

            # TimescaleDB policy senkronu — her iki hypertable da (tag_readings
            # ham veri, features türev) aynı kullanıcı tercihini takip eder.
            for hypertable in ("tag_readings", "features"):
                await conn.execute(
                    f"SELECT remove_retention_policy(    '{hypertable}', if_exists => true);",
                )
                if new_auto:
                    # INTERVAL değeri server-side oluşturulur; new_days int
                    # olduğu için SQL injection riski yok.
                    await conn.execute(
                        f"SELECT add_retention_policy("
                        f"    '{hypertable}', INTERVAL '{new_days} days');",
                    )

        await logger.ainfo(
            "Retention config güncellendi",
            raw_retention_days=new_days,
            auto_clean_enabled=new_auto,
            push_global_enabled=new_push_global,
            updated_by=updated_by,
        )
        return RetentionConfig(
            raw_retention_days=new_days,
            auto_clean_enabled=new_auto,
            push_global_enabled=new_push_global,
            updated_at=row["updated_at"],
            updated_by=row["updated_by"],
            global_maintenance_until=row["global_maintenance_until"],
            global_maintenance_reason=row["global_maintenance_reason"],
            global_maintenance_started_by_user_id=row[
                "global_maintenance_started_by_user_id"
            ],
            global_maintenance_started_at=row["global_maintenance_started_at"],
            resource_cpu_warn_pct=int(row["resource_cpu_warn_pct"]),
            resource_ram_warn_pct=int(row["resource_ram_warn_pct"]),
            ml_inference_enabled=bool(row["ml_inference_enabled"]),
            escalation_warn_to_crit_minutes=int(
                row["escalation_warn_to_crit_minutes"],
            ),
        )

    async def update_global_maintenance(
        self,
        until: datetime | None,
        reason: str,
        user_id: int | None,
        started_at: datetime | None,
    ) -> RetentionConfig:
        """Global bakım modu kolonlarını singleton satıra yazar (P-04).

        Push/retention alanlarına dokunmaz. Tek UPDATE — transaction'a
        gerek yok (TimescaleDB policy senkronu yok).
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "UPDATE retention_config SET "
                "    global_maintenance_until = $1, "
                "    global_maintenance_reason = $2, "
                "    global_maintenance_started_by_user_id = $3, "
                "    global_maintenance_started_at = $4 "
                "WHERE id = 1 "
                "RETURNING raw_retention_days, auto_clean_enabled, "
                "          push_global_enabled, updated_at, updated_by, "
                "          global_maintenance_until, global_maintenance_reason, "
                "          global_maintenance_started_by_user_id, "
                "          global_maintenance_started_at, "
                "          resource_cpu_warn_pct, resource_ram_warn_pct, "
                "          ml_inference_enabled, "
                "          escalation_warn_to_crit_minutes",
                until,
                reason,
                user_id,
                started_at,
            )
        assert row is not None, "retention_config satırı güncellenemedi"
        return RetentionConfig(
            raw_retention_days=int(row["raw_retention_days"]),
            auto_clean_enabled=bool(row["auto_clean_enabled"]),
            push_global_enabled=bool(row["push_global_enabled"]),
            updated_at=row["updated_at"],
            updated_by=row["updated_by"],
            global_maintenance_until=row["global_maintenance_until"],
            global_maintenance_reason=row["global_maintenance_reason"],
            global_maintenance_started_by_user_id=row[
                "global_maintenance_started_by_user_id"
            ],
            global_maintenance_started_at=row["global_maintenance_started_at"],
            resource_cpu_warn_pct=int(row["resource_cpu_warn_pct"]),
            resource_ram_warn_pct=int(row["resource_ram_warn_pct"]),
            ml_inference_enabled=bool(row["ml_inference_enabled"]),
            escalation_warn_to_crit_minutes=int(
                row["escalation_warn_to_crit_minutes"],
            ),
        )

    # --- Auth: users + sessions (V11-101) ---

    async def create_user(
        self,
        username: str,
        password_hash: str,
        role: str,
        must_change_password: bool = False,
    ) -> User:
        """Yeni kullanıcı kaydı oluşturur. UNIQUE çakışmasında asyncpg fırlatır."""
        if role not in ("operator", "developer"):
            msg = f"Geçersiz rol: {role!r} (operator|developer)"
            raise ValueError(msg)
        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO users "
                "(username, password_hash, role, must_change_password) "
                "VALUES ($1, $2, $3, $4) "
                "RETURNING id, username, role, enabled, must_change_password",
                username,
                password_hash,
                role,
                must_change_password,
            )
        assert row is not None
        return User(
            id=int(row["id"]),
            username=row["username"],
            role=row["role"],
            enabled=bool(row["enabled"]),
            must_change_password=bool(row["must_change_password"]),
        )

    async def get_user_by_username(self, username: str) -> User | None:
        """Username üzerinden kullanıcıyı döndürür."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, username, role, enabled, must_change_password "
                "FROM users WHERE username = $1",
                username,
            )
        if row is None:
            return None
        return User(
            id=int(row["id"]),
            username=row["username"],
            role=row["role"],
            enabled=bool(row["enabled"]),
            must_change_password=bool(row["must_change_password"]),
        )

    async def get_user_password_hash(self, username: str) -> str | None:
        """Login akışı: parola hash'i döndürür (kullanıcı yoksa None)."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT password_hash FROM users WHERE username = $1 AND enabled = TRUE",
                username,
            )
        if row is None:
            return None
        return str(row["password_hash"])

    async def update_user_password(
        self,
        user_id: int,
        new_password_hash: str,
    ) -> bool:
        """Parolayı günceller; must_change_password False yapar."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            result: str = await conn.execute(
                "UPDATE users SET "
                "    password_hash = $1, "
                "    must_change_password = FALSE, "
                "    updated_at = NOW() "
                "WHERE id = $2",
                new_password_hash,
                user_id,
            )
        # asyncpg execute "UPDATE N" döndürür; N=0 ise kayıt bulunamadı.
        return result.endswith(" 1")

    async def update_last_login(self, user_id: int) -> None:
        """``last_login_at = NOW()`` — sessizce best-effort."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET last_login_at = NOW() WHERE id = $1",
                user_id,
            )

    async def set_user_enabled(self, user_id: int, enabled: bool) -> bool:
        """Kullanıcıyı aktif/pasif yapar. enabled=False olunca login engellenir."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            result: str = await conn.execute(
                "UPDATE users SET enabled = $1, updated_at = NOW() WHERE id = $2",
                enabled,
                user_id,
            )
        return result.endswith(" 1")

    async def list_users(self) -> list[User]:
        """Tüm kullanıcıları döndürür (id ASC)."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, username, role, enabled, must_change_password "
                "FROM users ORDER BY id ASC",
            )
        return [
            User(
                id=int(r["id"]),
                username=r["username"],
                role=r["role"],
                enabled=bool(r["enabled"]),
                must_change_password=bool(r["must_change_password"]),
            )
            for r in rows
        ]

    async def create_session(
        self,
        user_id: int,
        token: str,
        expires_at: datetime,
        ip_addr: str = "",
        user_agent: str = "",
    ) -> Session:
        """Yeni oturum kaydı + JOIN users ile snapshot döndürür."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "WITH new_session AS ("
                "    INSERT INTO sessions "
                "    (user_id, token, expires_at, ip_addr, user_agent) "
                "    VALUES ($1, $2, $3, $4, $5) "
                "    RETURNING id, user_id, expires_at"
                ") "
                "SELECT s.id, s.user_id, u.username, u.role, "
                "       u.enabled, u.must_change_password, s.expires_at "
                "FROM new_session s JOIN users u ON u.id = s.user_id",
                user_id,
                token,
                expires_at,
                ip_addr,
                user_agent,
            )
        assert row is not None
        return Session(
            id=int(row["id"]),
            user_id=int(row["user_id"]),
            username=row["username"],
            role=row["role"],
            enabled=bool(row["enabled"]),
            must_change_password=bool(row["must_change_password"]),
            expires_at=row["expires_at"],
        )

    async def get_session_by_token(self, token: str) -> Session | None:
        """Cookie token → Session snapshot. Süresi geçmiş veya pasif user → None.

        Tek query; auth dependency her request'te çağırır, indeks
        ``idx_sessions_token`` üzerinden.
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT s.id, s.user_id, u.username, u.role, "
                "       u.enabled, u.must_change_password, s.expires_at "
                "FROM sessions s JOIN users u ON u.id = s.user_id "
                "WHERE s.token = $1 "
                "  AND s.expires_at > NOW() "
                "  AND u.enabled = TRUE",
                token,
            )
        if row is None:
            return None
        return Session(
            id=int(row["id"]),
            user_id=int(row["user_id"]),
            username=row["username"],
            role=row["role"],
            enabled=bool(row["enabled"]),
            must_change_password=bool(row["must_change_password"]),
            expires_at=row["expires_at"],
        )

    async def delete_session(self, token: str) -> bool:
        """Logout — token'ı siler."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            result: str = await conn.execute(
                "DELETE FROM sessions WHERE token = $1",
                token,
            )
        return result.endswith(" 1")

    async def cleanup_expired_sessions(self) -> int:
        """Süresi dolmuş session'ları toplu siler. Dönüş: silinen kayıt sayısı."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            result: str = await conn.execute(
                "DELETE FROM sessions WHERE expires_at < NOW()",
            )
        # "DELETE N" formatından N'i çek
        try:
            return int(result.split()[-1])
        except (ValueError, IndexError):
            return 0

    async def count_recent_failed_logins(
        self,
        ip_address: str,
        since: datetime,
    ) -> int:
        """PP-06 — IP başına başarısız login sayımı (rate limiting için).

        ``audit_log.detail`` formatı ``ip=<addr> user=<name> reason=<r>`` ile
        prefix-match yapar. Boş IP ('' istemci yoksa) hiçbir kaydı eşleştirmez
        — saldırgan IP gizleyemez (proxy varsa X-Forwarded-For ileride
        eklenebilir; pilot LAN'ında doğrudan istemci yeterli).
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            count: int | None = await conn.fetchval(
                """
                SELECT COUNT(*) FROM audit_log
                WHERE category = 'auth'
                  AND action = 'login_failed'
                  AND timestamp >= $1
                  AND detail LIKE 'ip=' || $2 || ' %'
                """,
                since,
                ip_address,
            )
        return count or 0

    async def count_recent_failed_logins_by_username(
        self,
        username: str,
        since: datetime,
    ) -> int:
        """H-2 (29 Nis 2026 denetim) — username başına başarısız login sayımı.

        IP-bazlı sayımı tamamlar (dağıtık brute-force koruması). LIKE
        wildcard karakterlerini (``\\``, ``%``, ``_``) escape edip
        ``user=<username> reason=`` token'ını arar.
        """
        # LIKE wildcard'larını username'den kaçır — \ önce gelmeli, çünkü
        # sonraki replace'lerde eklenen \'ları çiftlememek için.
        escaped_user = (
            username.replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        pattern = f"% user={escaped_user} reason=%"
        pool = self._get_pool()
        async with pool.acquire() as conn:
            count: int | None = await conn.fetchval(
                r"""
                SELECT COUNT(*) FROM audit_log
                WHERE category = 'auth'
                  AND action = 'login_failed'
                  AND timestamp >= $1
                  AND detail LIKE $2 ESCAPE '\'
                """,
                since,
                pattern,
            )
        return count or 0

    # --- Service Heartbeats (V11-105/K13) ---

    async def write_service_heartbeat(
        self,
        service_name: str,
        status: str = "active",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Service heartbeat upsert — last_heartbeat_at = NOW()."""
        pool = self._get_pool()
        # JSONB için metadata'yı string'e serialize et (None ise NULL).
        meta_json = json.dumps(metadata) if metadata is not None else None
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO service_heartbeats (
                    service_name, last_heartbeat_at, status, metadata
                )
                VALUES ($1, NOW(), $2, $3::jsonb)
                ON CONFLICT (service_name) DO UPDATE SET
                    last_heartbeat_at = NOW(),
                    status = EXCLUDED.status,
                    metadata = EXCLUDED.metadata;
                """,
                service_name,
                status,
                meta_json,
            )

    async def list_service_heartbeats(self) -> list[ServiceHeartbeat]:
        """Tüm servis heartbeat kayıtlarını döner."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT service_name, last_heartbeat_at, status, metadata
                FROM service_heartbeats
                ORDER BY service_name;
                """,
            )
        result: list[ServiceHeartbeat] = []
        for row in rows:
            raw_meta = row["metadata"]
            # asyncpg JSONB → str (registered codec yoksa); dict de olabilir.
            meta: dict[str, Any] | None
            if raw_meta is None:
                meta = None
            elif isinstance(raw_meta, dict):
                meta = raw_meta
            else:
                try:
                    meta = json.loads(raw_meta)
                except (TypeError, ValueError):
                    meta = None
            result.append(
                ServiceHeartbeat(
                    service_name=row["service_name"],
                    last_heartbeat_at=row["last_heartbeat_at"],
                    status=row["status"],
                    metadata=meta,
                )
            )
        return result

    # --- Cross-Sensor Rules implementasyonu (R-06 / V11-305) ---

    async def insert_cross_sensor_rule(
        self,
        rule: CrossSensorRule,
    ) -> CrossSensorRule:
        """Yeni cross-sensor kuralı kaydeder.

        ``operator`` ve ``severity`` Migration 036 CHECK constraint'leri ile
        sınırlı; geçersiz değer asyncpg ``CheckViolationError`` fırlatır.
        ``tag_a_id`` == ``tag_b_id`` ise DB CHECK constraint reddeder.
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO cross_sensor_rules "
                "(name, tag_a_id, tag_b_id, operator, severity, "
                "enabled, description) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7) "
                "RETURNING *",
                rule.name,
                rule.tag_a_id,
                rule.tag_b_id,
                rule.operator,
                rule.severity,
                rule.enabled,
                rule.description,
            )
        assert row is not None
        return _row_to_cross_sensor_rule(row)

    async def update_cross_sensor_rule(
        self,
        rule_id: int,
        updates: dict[str, object],
    ) -> CrossSensorRule | None:
        """Kuralı günceller — whitelist dışı alan ValueError fırlatır."""
        invalid = set(updates.keys()) - _ALLOWED_CROSS_SENSOR_UPDATE_FIELDS
        if invalid:
            msg = f"Güncellenemeyen alanlar: {invalid}"
            raise ValueError(msg)

        if not updates:
            return await self.get_cross_sensor_rule(rule_id)

        set_parts: list[str] = []
        values: list[object] = []
        for i, (col, val) in enumerate(updates.items(), start=1):
            set_parts.append(f"{col} = ${i}")
            values.append(val)

        idx = len(values) + 1
        set_parts.append(f"updated_at = ${idx}")
        values.append(datetime.now(UTC))

        idx_where = len(values) + 1
        values.append(rule_id)

        sql = (
            f"UPDATE cross_sensor_rules SET {', '.join(set_parts)} "
            f"WHERE id = ${idx_where} RETURNING *"
        )

        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(sql, *values)

        if row is None:
            return None
        return _row_to_cross_sensor_rule(row)

    async def delete_cross_sensor_rule(self, rule_id: int) -> bool:
        """Kuralı siler."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM cross_sensor_rules WHERE id = $1",
                rule_id,
            )
        return str(result) == "DELETE 1"

    async def get_cross_sensor_rule(
        self,
        rule_id: int,
    ) -> CrossSensorRule | None:
        """Tek kuralı döndürür."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM cross_sensor_rules WHERE id = $1",
                rule_id,
            )
        if row is None:
            return None
        return _row_to_cross_sensor_rule(row)

    async def list_cross_sensor_rules(
        self,
        enabled: bool | None = None,
    ) -> list[CrossSensorRule]:
        """Kural listesi (id ASC). ``enabled=True`` engine cache'i için."""
        pool = self._get_pool()
        if enabled is None:
            sql = "SELECT * FROM cross_sensor_rules ORDER BY id"
            params: tuple[object, ...] = ()
        else:
            sql = "SELECT * FROM cross_sensor_rules WHERE enabled = $1 ORDER BY id"
            params = (enabled,)
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
        return [_row_to_cross_sensor_rule(row) for row in rows]

    # --- SPC State implementasyonlari (R-07 / V11-308) ---

    async def get_spc_state(self, tag_id: str) -> SpcState | None:
        """Tek tag icin SPC state kaydini getirir."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM spc_state WHERE tag_id = $1",
                tag_id,
            )
        if row is None:
            return None
        return _row_to_spc_state(row)

    async def upsert_spc_state(self, state: SpcState) -> SpcState:
        """SPC state'ini ekler ya da gunceller. ``tag_id`` UNIQUE."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO spc_state "
                "(tag_id, sample_count, ewma_value, ewma_variance, "
                "cusum_pos, cusum_neg, mad_median, mad_value, "
                "last_sample_at, learning_complete, updated_at) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11) "
                "ON CONFLICT (tag_id) DO UPDATE SET "
                "sample_count = EXCLUDED.sample_count, "
                "ewma_value = EXCLUDED.ewma_value, "
                "ewma_variance = EXCLUDED.ewma_variance, "
                "cusum_pos = EXCLUDED.cusum_pos, "
                "cusum_neg = EXCLUDED.cusum_neg, "
                "mad_median = EXCLUDED.mad_median, "
                "mad_value = EXCLUDED.mad_value, "
                "last_sample_at = EXCLUDED.last_sample_at, "
                "learning_complete = EXCLUDED.learning_complete, "
                "updated_at = EXCLUDED.updated_at "
                "RETURNING *",
                state.tag_id,
                state.sample_count,
                state.ewma_value,
                state.ewma_variance,
                state.cusum_pos,
                state.cusum_neg,
                state.mad_median,
                state.mad_value,
                state.last_sample_at,
                state.learning_complete,
                datetime.now(UTC),
            )
        assert row is not None  # INSERT/UPDATE RETURNING her zaman satir doner
        return _row_to_spc_state(row)

    async def list_spc_states(self) -> list[SpcState]:
        """Tum SPC state kayitlarini doner."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM spc_state ORDER BY tag_id")
        return [_row_to_spc_state(row) for row in rows]


def _row_to_spc_state(row: asyncpg.Record) -> SpcState:
    """asyncpg satirini SpcState'e donusturur (R-07 / V11-308)."""
    raw_ewma = row["ewma_value"]
    raw_variance = row["ewma_variance"]
    raw_median = row["mad_median"]
    raw_mad = row["mad_value"]
    return SpcState(
        id=row["id"],
        tag_id=row["tag_id"],
        sample_count=row["sample_count"],
        ewma_value=float(raw_ewma) if raw_ewma is not None else None,
        ewma_variance=float(raw_variance) if raw_variance is not None else None,
        cusum_pos=float(row["cusum_pos"]),
        cusum_neg=float(row["cusum_neg"]),
        mad_median=float(raw_median) if raw_median is not None else None,
        mad_value=float(raw_mad) if raw_mad is not None else None,
        last_sample_at=row["last_sample_at"],
        learning_complete=bool(row["learning_complete"]),
        updated_at=row["updated_at"],
    )


def _row_to_overview_chart_tag(row: asyncpg.Record) -> OverviewChartTag:
    """asyncpg satirini OverviewChartTag'e donusturur."""
    return OverviewChartTag(
        id=row["id"],
        chart_key=row["chart_key"],
        tag_id=row["tag_id"],
        sort_order=row["sort_order"],
        created_at=row["created_at"],
    )


def _row_to_overview_chart(row: asyncpg.Record) -> OverviewChart:
    """asyncpg satirini OverviewChart'a donusturur."""
    return OverviewChart(
        chart_key=row["chart_key"],
        title=row["title"],
        sort_order=row["sort_order"],
        time_window_minutes=row["time_window_minutes"],
        created_at=row["created_at"],
    )


# Overview chart update icin izin verilen alanlar (SQL injection onlemi)
_ALLOWED_OVERVIEW_CHART_UPDATE_FIELDS: frozenset[str] = frozenset(
    {
        "title",
        "sort_order",
        "time_window_minutes",
    }
)


# Maintenance update whitelist'leri (SQL injection önlemi)
_ALLOWED_CHECKLIST_UPDATE_FIELDS: frozenset[str] = frozenset(
    {
        "title",
        "description",
        "category",
        "asset_template_id",
    }
)

_ALLOWED_SCHEDULE_UPDATE_FIELDS: frozenset[str] = frozenset(
    {
        "checklist_id",
        "asset_template_id",
        "asset_instance_id",
        "period_kind",
        "period_value",
        "anchor_date",
        "next_due_at",
        "notify_lead_hours",
        "enabled",
    }
)

_ALLOWED_TASK_UPDATE_FIELDS: frozenset[str] = frozenset(
    {
        "status",
        "started_at",
        "completed_at",
        "completed_by",
        "notes",
        "due_at",
    }
)


def _row_to_maintenance_checklist(
    row: asyncpg.Record,
) -> MaintenanceChecklist:
    """asyncpg satırını MaintenanceChecklist'e dönüştürür (steps hariç)."""
    return MaintenanceChecklist(
        id=row["id"],
        slug=row["slug"],
        title=row["title"],
        description=row["description"],
        category=row["category"],
        asset_template_id=row["asset_template_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_maintenance_step(
    row: asyncpg.Record,
) -> MaintenanceChecklistStep:
    """asyncpg satırını MaintenanceChecklistStep'e dönüştürür."""
    return MaintenanceChecklistStep(
        id=row["id"],
        checklist_id=row["checklist_id"],
        sort_order=row["sort_order"],
        text=row["text"],
        estimated_minutes=row["estimated_minutes"],
        created_at=row["created_at"],
    )


def _row_to_maintenance_schedule(
    row: asyncpg.Record,
) -> MaintenanceSchedule:
    """asyncpg satırını MaintenanceSchedule'a dönüştürür."""
    return MaintenanceSchedule(
        id=row["id"],
        checklist_id=row["checklist_id"],
        asset_template_id=row["asset_template_id"],
        asset_instance_id=row["asset_instance_id"],
        period_kind=row["period_kind"],
        period_value=row["period_value"],
        anchor_date=row["anchor_date"],
        next_due_at=row["next_due_at"],
        notify_lead_hours=row["notify_lead_hours"],
        enabled=row["enabled"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_maintenance_task(row: asyncpg.Record) -> MaintenanceTask:
    """asyncpg satırını MaintenanceTask'e dönüştürür."""
    return MaintenanceTask(
        id=row["id"],
        schedule_id=row["schedule_id"],
        checklist_id=row["checklist_id"],
        asset_instance_id=row["asset_instance_id"],
        source=row["source"],
        alarm_event_id=row["alarm_event_id"],
        title_snapshot=row["title_snapshot"],
        due_at=row["due_at"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        completed_by=row["completed_by"],
        notes=row["notes"],
        status=row["status"],
        created_at=row["created_at"],
    )


def _row_to_maintenance_step_result(
    row: asyncpg.Record,
) -> MaintenanceTaskStepResult:
    """asyncpg satırını MaintenanceTaskStepResult'a dönüştürür."""
    return MaintenanceTaskStepResult(
        id=row["id"],
        task_id=row["task_id"],
        step_id=row["step_id"],
        checked=row["checked"],
        note=row["note"],
        completed_at=row["completed_at"],
    )


def _row_to_alarm_checklist_mapping(
    row: asyncpg.Record,
) -> AlarmChecklistMapping:
    """asyncpg satırını AlarmChecklistMapping'e dönüştürür."""
    return AlarmChecklistMapping(
        id=row["id"],
        threshold_id=row["threshold_id"],
        checklist_id=row["checklist_id"],
        created_at=row["created_at"],
    )


def create_database(settings: Settings) -> DatabaseInterface:
    """Veritabanı instance'ı oluşturan factory fonksiyonu.

    Şu an her zaman TimescaleDBDatabase döndürür. Abstract tip
    döndürdüğü için ileride başka implementasyonlara geçiş kolaydır.
    """
    return TimescaleDBDatabase(settings)
