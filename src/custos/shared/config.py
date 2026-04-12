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
