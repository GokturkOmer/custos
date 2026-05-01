"""Konfigürasyon modülü.

Pydantic Settings ile .env dosyasından ortam değişkenlerini okur
ve tip güvenli erişim sağlar.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Custos uygulama ayarları.

    .env dosyasından otomatik okunur. Dosya yoksa ortam
    değişkenlerinden veya varsayılan değerlerden beslenir.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    postgres_db: str = "custos"
    postgres_user: str = "custos"
    postgres_password: str = "degistir-bu-bir-ornektir"
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    log_level: str = "INFO"

    # DB user ayrımı (V11-106/K14). Yeni kurulumlarda setup.sh iki ayrı user
    # ve iki DSN üretir:
    #   custos_db_dsn        → runtime (custos_app, sadece DML — DDL yetkisi yok)
    #   custos_db_admin_dsn  → migration (custos_admin, owner — DDL/GRANT/REVOKE)
    # Geriye dönük uyum: ikisi de boş ise klasik POSTGRES_* değişkenlerinden
    # database_url üzerinden tek user deseni kullanılır (lokal dev, mevcut
    # pilot-öncesi kurulumlar). alembic/env.py admin DSN'i öncelikli okur.
    custos_db_dsn: str = ""
    custos_db_admin_dsn: str = ""

    # PP-09 (29 Nis 2026): Pytest integration testleri için ayrı DSN.
    # Boşsa runtime DSN'e fallback yapılır (geriye uyumlu); set edilince
    # integration testleri sadece bu DSN'e yazar — pilot/dev DB'sine
    # TEST_ prefix'li satır sızıntısı riski kapanır. Önerilen pattern:
    #   CUSTOS_TEST_DSN=postgresql://custos:test@localhost:5433/custos_test
    custos_test_dsn: str = ""

    # VAPID push bildirim ayarları
    custos_vapid_private_key: str = ""
    custos_vapid_public_key: str = ""
    custos_vapid_mailto: str = "mailto:admin@custos.local"

    # Sessiz saat hesabı için yerel zaman dilimi (IANA formatı)
    custos_timezone: str = "Europe/Istanbul"

    # H-1 (29 Nis 2026 denetim): TrustedHostMiddleware allow-list. Caddy
    # ardındaki uvicorn Host header injection saldırılarını eler. CSV format:
    #   CUSTOS_ALLOWED_HOSTS=192.168.1.10,custos.local
    # Boş bırakılırsa middleware eklenmez (lokal dev/test). Production
    # setup.sh setup'ta CUSTOS_HOST_IP'yi tahmin ediyor — pilot deploy'da
    # operator bu değeri açıkça set etmeli.
    custos_allowed_hosts: str = ""

    # H-1 ek (29 Nis 2026 denetim): Production deploy IP'si (Caddy + TLS
    # konfigürasyonu için). Set edilmişse "production mode" — Secure cookie
    # zorlama + dev escape hatch reddi (__main__.py startup guard'ı).
    custos_host_ip: str = ""

    # Collector per-host paralel okuma üst sınırı (Semaphore). Modbus slave'lerin
    # tipik max concurrent connection sınırı 8-32; 5 güvenli başlangıç.
    collector_per_host_concurrency: int = 5

    # Fast polling bütçesi — polling_interval_ms <= 1000 olan aktif tag sayısı.
    # Aşım init veya activation'da hata olarak reddedilir.
    # Default 60: pilot 200 tag gerçeği (v1.0.1 kalem 17). .env.example ile
    # senkron. v1.1'de threshold semantiği (1000ms "fast" mı?) revize edilecek.
    collector_fast_polling_budget: int = 60

    # Batch Modbus read (F11 Paket I). Komşu register'ları tek
    # read_holding_registers çağrısında okur, PLC round-trip'i ~10x azaltır.
    # Acil geri dönüş için feature flag: False -> eski per-tag yol.
    collector_batch_read_enabled: bool = True

    # Batch gruplama gap toleransı (register adres boşluğu). 0 -> sadece
    # tam ardışık adresler birleşir; 8 -> aradaki 8 register'lık boşluk
    # tek batch'te okunur (dummy register'lar decode edilmez, atlanır).
    # Saha'da register haritasına göre tune edilir, re-deploy gerekmez.
    collector_batch_gap_tolerance: int = 8

    # Query guard eşikleri (F11 Paket H). Pilot saatinde aşırı geniş sorgular
    # (200 tag × 2 yıl × ham gibi) sistemin cevap süresini patlatmasın diye
    # `query_readings_auto` içinde katman override / reject ile uygulanır.
    # raw ve 1min için eşik `tag_count × time_range_days` yüküne bakar;
    # 1hour katmanında sadece uzun pencere reddedilir.
    query_guard_raw_max_tag_days: float = 7.0
    query_guard_1min_max_tag_days: float = 200.0
    query_guard_1hour_max_days: float = 3650.0  # ~10 yıl

    # Disk doluluk widget'ı + DiskMonitor tick'i izlenen mount point
    # (v1.0.1 borç #2). Pilot deploy'da PostgreSQL data dir'i veya
    # parquet arşiv mount'u olmalı; dev'de path yoksa shutil.disk_usage
    # FileNotFoundError fırlatır ve DiskMonitor sessizce geçer.
    custos_disk_monitor_path: str = "/var/custos"

    # Teknik asistan chatbot (F8b) ayarları.
    # knowledge_dir: Bilgi tabanı kök dizini — Markdown + YAML dokümanlar burada.
    # knowledge_local_dir: Saha-spesifik (lokal) dizin (V11-110, K6 hibrit).
    #   git'le birleştirilir; aynı slug local'de varsa override eder.
    #   setup.sh kurar (chown custos:custos, chmod 0750). Dev makinesinde
    #   dizin yoksa loader sessizce atlar.
    # score_threshold: Semantic search'te minimum cosine benzerlik; altındaysa
    #   "bilgi bulamadım" döner. 0.35 brief §4.9'daki multilingual model için
    #   makul başlangıç değeridir (pilot sırasında ayarlanabilir).
    # top_k: Search'ten dönen en yakın chunk sayısı. Kaynak link üretimi için
    #   şimdilik en yüksek skorlu olan kullanılır; 3 future-proof.
    custos_assistant_knowledge_dir: str = "data/knowledge"
    custos_assistant_knowledge_local_dir: str = "/var/custos/knowledge/local"
    custos_assistant_score_threshold: float = 0.60
    custos_assistant_top_k: int = 3

    @property
    def database_url(self) -> str:
        """PostgreSQL bağlantı URL'sini döndürür (client_encoding=utf8 dahil).

        Öncelik:
        1. ``custos_db_dsn`` (yeni runtime DSN, V11-106 — query string olarak
           ``client_encoding=utf8`` eklenir).
        2. POSTGRES_* legacy değişkenleri (eski tek-user deseni, dev/lokal).
        """
        if self.custos_db_dsn:
            return _ensure_client_encoding(self.custos_db_dsn)
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}"
            f"/{self.postgres_db}?client_encoding=utf8"
        )

    @property
    def database_url_async(self) -> str:
        """asyncpg için bağlantı URL'sini döndürür (postgresql:// şeması)."""
        if self.custos_db_dsn:
            return _strip_query(self.custos_db_dsn)
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}"
            f"/{self.postgres_db}"
        )

    @property
    def allowed_hosts_list(self) -> list[str]:
        """``custos_allowed_hosts`` CSV'i listeye çevirir; boş elemanları atar.

        Boş döndürürse __main__.py TrustedHostMiddleware eklemez — lokal
        dev/testte Host header'ı kontrol edilmez (TestClient ``testserver``
        gönderir). Pilot deploy'da bu değer set edilmelidir.
        """
        return [h.strip() for h in self.custos_allowed_hosts.split(",") if h.strip()]

    @property
    def database_admin_url(self) -> str:
        """Migration DSN — alembic/env.py tarafından kullanılır (V11-106).

        ``custos_db_admin_dsn`` tanımlıysa onu döner (DDL/GRANT yetkili
        custos_admin user'ı). Yoksa ``database_url`` fallback (lokal dev,
        eski tek-user kurulumları) — bu durumda alembic mevcut DSN ile
        çalışır.
        """
        if self.custos_db_admin_dsn:
            return _ensure_client_encoding(self.custos_db_admin_dsn)
        return self.database_url


def _ensure_client_encoding(dsn: str) -> str:
    """DSN'in query string'ine ``client_encoding=utf8`` ekler (yoksa)."""
    if "client_encoding=" in dsn:
        return dsn
    sep = "&" if "?" in dsn else "?"
    return f"{dsn}{sep}client_encoding=utf8"


def _strip_query(dsn: str) -> str:
    """Query string'i kaldırır — asyncpg query string desteklemez."""
    if "?" in dsn:
        return dsn.split("?", 1)[0]
    return dsn


settings = Settings()
