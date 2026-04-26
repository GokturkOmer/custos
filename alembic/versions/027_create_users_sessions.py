"""users + sessions tabloları — Auth + 2 rol (V11-101).

v1.1 Paket 01 — pilot öncesi sertleştirme. Custos dashboard'ına oturum +
rol-tabanlı erişim kontrolü eklenir. K1 kararı: 2 rol (operator + developer).

- ``users``        : kimlik + bcrypt parola + rol + ilk-giriş bayrağı
- ``sessions``     : aktif çerez token'ları (12 saat TTL, app-side cleanup)

Bcrypt hash 12 round; token ``secrets.token_urlsafe(32)`` (43 char). Session
süresi runtime tarafında set edilir (app default 12 saat) — DB constraint yok.
``cleanup_expired_sessions`` bir günlük worker tarafından çağrılır.

Pilot 5 Haziran 2026'da Torunlar GYO AVM'sinde kullanılır; bu migration'dan
önce dashboard'da hiçbir auth kontrolü yoktur (LAN'daki herkes threshold
silebilir, retention'ı 30 güne çekebilir, alarmları temizleyebilir).

Revision ID: 027
Revises: 026
Create Date: 2026-04-26
"""

from collections.abc import Sequence

from alembic import op

revision: str = "027"
down_revision: str | None = "026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """users + sessions tablolarını ve indekslerini oluştur."""
    op.execute(
        """
        CREATE TABLE users (
            id SERIAL PRIMARY KEY,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL
                CHECK (role IN ('operator', 'developer')),
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            must_change_password BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_login_at TIMESTAMPTZ
        );
        """
    )
    op.execute(
        """
        CREATE TABLE sessions (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL
                REFERENCES users(id) ON DELETE CASCADE,
            token TEXT NOT NULL UNIQUE,
            expires_at TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            ip_addr TEXT NOT NULL DEFAULT '',
            user_agent TEXT NOT NULL DEFAULT ''
        );
        """
    )
    # token: cookie -> session lookup'ı her request'te yapılır, indeks zorunlu.
    # expires_at: cleanup worker WHERE expires_at < NOW() taraması.
    op.execute("CREATE INDEX idx_sessions_token ON sessions(token);")
    op.execute("CREATE INDEX idx_sessions_expires ON sessions(expires_at);")


def downgrade() -> None:
    """Tabloları kaldır (sessions FK'sı CASCADE ile temizlenir)."""
    op.execute("DROP TABLE IF EXISTS sessions;")
    op.execute("DROP TABLE IF EXISTS users;")
