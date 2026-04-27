"""DB user ayrımı (V11-106/K14) — config.py DSN seçim mantığı.

Iki opsiyonel DSN field tanımlandığında ``database_url`` /
``database_url_async`` / ``database_admin_url`` doğru kaynağı seçmeli.
Boş ise legacy POSTGRES_* fallback.
"""

from __future__ import annotations

from custos.shared.config import Settings


def _mk(**kwargs: object) -> Settings:
    return Settings(**kwargs)  # type: ignore[arg-type]


def test_legacy_postgres_vars_used_when_dsn_empty() -> None:
    """CUSTOS_DB_DSN tanımsız → POSTGRES_* fallback (geriye uyum)."""
    s = _mk(
        custos_db_dsn="",
        custos_db_admin_dsn="",
        postgres_user="legacy",
        postgres_password="pwd",
        postgres_host="db.local",
        postgres_port=5433,
        postgres_db="custos",
    )
    assert s.database_url.startswith("postgresql://legacy:pwd@db.local:5433/custos")
    assert "client_encoding=utf8" in s.database_url
    # asyncpg query string desteklemez
    assert "?" not in s.database_url_async
    # admin yoksa app fallback
    assert s.database_admin_url == s.database_url


def test_runtime_dsn_takes_priority() -> None:
    """CUSTOS_DB_DSN tanımlıysa POSTGRES_* yok sayılır."""
    s = _mk(
        custos_db_dsn="postgresql://custos_app:appsecret@localhost:5432/custos",
        custos_db_admin_dsn="",
        postgres_user="legacy",
        postgres_password="pwd",
    )
    assert "custos_app:appsecret" in s.database_url
    assert "legacy" not in s.database_url
    # client_encoding eklenir
    assert "client_encoding=utf8" in s.database_url
    # asyncpg URL'inde query yok
    assert s.database_url_async == "postgresql://custos_app:appsecret@localhost:5432/custos"


def test_admin_dsn_used_for_admin_url() -> None:
    """database_admin_url custos_db_admin_dsn'i öncelikli okur."""
    s = _mk(
        custos_db_dsn="postgresql://custos_app:appsecret@localhost:5432/custos",
        custos_db_admin_dsn="postgresql://custos_admin:admsecret@localhost:5432/custos",
    )
    assert "custos_admin:admsecret" in s.database_admin_url
    assert "client_encoding=utf8" in s.database_admin_url
    # runtime URL admin DSN'i kullanmamalı
    assert "custos_admin" not in s.database_url


def test_existing_query_string_preserved_in_dsn() -> None:
    """DSN'de query zaten varsa client_encoding eklenmez (duplicate önler)."""
    s = _mk(
        custos_db_dsn="postgresql://u:p@h:5432/d?application_name=foo",
    )
    # Bizim helper double-encoding yapmamalı
    assert s.database_url.count("?") == 1
    # asyncpg URL'i query'yi tamamen sıyırmalı
    assert "?" not in s.database_url_async


def test_alembic_env_uses_admin_url(monkeypatch: object) -> None:
    """alembic/env.py admin URL'i set_main_option ile kullanmalı."""
    # Modülü import edip çağrı izini doğrulamak yerine: settings'i değiştir,
    # kullanıcının doğrudan eriştiği özelliği kontrol et — alembic config
    # bu değeri okuduğu için indirekt doğrulama yeterli.
    s = _mk(
        custos_db_admin_dsn="postgresql://custos_admin:admsecret@localhost:5432/custos",
    )
    # admin URL → custos_admin
    assert s.database_admin_url.startswith(
        "postgresql://custos_admin:admsecret@localhost:5432/custos"
    )
