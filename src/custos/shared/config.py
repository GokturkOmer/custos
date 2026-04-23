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

    # Teknik asistan chatbot (F8b) ayarları.
    # knowledge_dir: Bilgi tabanı kök dizini — Markdown + YAML dokümanlar burada.
    # score_threshold: Semantic search'te minimum cosine benzerlik; altındaysa
    #   "bilgi bulamadım" döner. 0.35 brief §4.9'daki multilingual model için
    #   makul başlangıç değeridir (pilot sırasında ayarlanabilir).
    # top_k: Search'ten dönen en yakın chunk sayısı. Kaynak link üretimi için
    #   şimdilik en yüksek skorlu olan kullanılır; 3 future-proof.
    custos_assistant_knowledge_dir: str = "data/knowledge"
    custos_assistant_score_threshold: float = 0.60
    custos_assistant_top_k: int = 3

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
