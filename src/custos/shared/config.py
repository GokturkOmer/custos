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

    # VAPID push bildirim ayarları
    custos_vapid_private_key: str = ""
    custos_vapid_public_key: str = ""
    custos_vapid_mailto: str = "mailto:admin@custos.local"

    # Sessiz saat hesabı için yerel zaman dilimi (IANA formatı)
    custos_timezone: str = "Europe/Istanbul"

    # Collector per-host paralel okuma üst sınırı (Semaphore). Modbus slave'lerin
    # tipik max concurrent connection sınırı 8-32; 5 güvenli başlangıç.
    collector_per_host_concurrency: int = 5

    # Fast polling bütçesi — polling_interval_ms <= 1000 olan aktif tag sayısı.
    # Aşım init veya activation'da hata olarak reddedilir.
    collector_fast_polling_budget: int = 10

    # Query guard eşikleri (F11 Paket H). Pilot saatinde aşırı geniş sorgular
    # (200 tag × 2 yıl × ham gibi) sistemin cevap süresini patlatmasın diye
    # `query_readings_auto` içinde katman override / reject ile uygulanır.
    # raw ve 1min için eşik `tag_count × time_range_days` yüküne bakar;
    # 1hour katmanında sadece uzun pencere reddedilir.
    query_guard_raw_max_tag_days: float = 7.0
    query_guard_1min_max_tag_days: float = 200.0
    query_guard_1hour_max_days: float = 3650.0  # ~10 yıl

    @property
    def database_url(self) -> str:
        """PostgreSQL bağlantı URL'sini döndürür (client_encoding=utf8 dahil)."""
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}"
            f"/{self.postgres_db}?client_encoding=utf8"
        )

    @property
    def database_url_async(self) -> str:
        """asyncpg için bağlantı URL'sini döndürür (postgresql:// şeması)."""
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}"
            f"/{self.postgres_db}"
        )


settings = Settings()
