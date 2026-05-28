"""Asistan servisi `assistant` PostgreSQL şeması (Faz 0 / karar B-C).

Asistan AYRI bir süreçtir ve kendi `repository.py` data-access katmanından
yalnızca bu şemaya erişir (`public`'e dokunmaz — karar A). Tek alembic
zincirinde 038 numarası (head `037_mode_aware_spc`; `026` zaten
`retention_config`).

Üç tablo (kanonik plan §3):
- ``documents`` — yüklenen PDF manuel metadata'sı.
- ``chunks``    — sayfa-bazlı chunk (text + PNG yolu + FAISS index id eşlemesi).
- ``queries_log`` — sorgu metriği (UX; ``user_id`` yalnız metrik, FK YOK).

GRANT'ler: migration prod'da owner (`custos_admin`) ile koşar; runtime rolü
`custos_app`'e USAGE + DML verilir. Ancak lokal/dev tek-user kurulumlarda
`custos_app` rolü YOKTUR — bu yüzden GRANT'ler ``pg_roles`` guard'lı bir
DO bloğuna sarılır (rol yoksa no-op). Böylece rollback drill ve pytest dev'de
de yeşil kalır; prod'da setup.sh'in `public` için yaptığı GRANT deseniyle
tutarlıdır.

Revision ID: 038
Revises: 037
Create Date: 2026-05-29
"""

from collections.abc import Sequence

from alembic import op

revision: str = "038"
down_revision: str | None = "037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """`assistant` şeması + 3 tablo + (rol varsa) custos_app GRANT'leri."""
    op.execute("CREATE SCHEMA IF NOT EXISTS assistant;")

    # documents — yüklenen manuel başına bir satır. uploaded_at TIMESTAMPTZ
    # (UTC; CLAUDE.md). equipment_type pilotta serbest metin (chiller/ahu/...).
    op.execute(
        """
        CREATE TABLE assistant.documents (
            document_id     SERIAL PRIMARY KEY,
            filename        TEXT NOT NULL,
            equipment_model TEXT,
            equipment_type  TEXT,
            language        TEXT,
            total_pages     INT,
            ocr_used        BOOLEAN NOT NULL DEFAULT FALSE,
            source_pdf_path TEXT,
            uploaded_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            uploaded_by     TEXT
        );
        """
    )

    # chunks — bir sayfa = bir chunk. faiss_index_id UNIQUE: FAISS vektör id'si
    # ile birebir eşleşme (retrieval'da chunk geri çözümü). document silinince
    # CASCADE ile chunk'lar düşer.
    op.execute(
        """
        CREATE TABLE assistant.chunks (
            chunk_id       SERIAL PRIMARY KEY,
            document_id    INT REFERENCES assistant.documents(document_id) ON DELETE CASCADE,
            page_no        INT,
            text_content   TEXT,
            png_path       TEXT,
            section_title  TEXT,
            faiss_index_id INT UNIQUE,
            has_table      BOOLEAN NOT NULL DEFAULT FALSE,
            has_figure     BOOLEAN NOT NULL DEFAULT FALSE
        );
        """
    )
    op.execute("CREATE INDEX idx_chunks_document ON assistant.chunks(document_id);")
    op.execute(
        "CREATE INDEX idx_documents_equipment "
        "ON assistant.documents(equipment_type, equipment_model);"
    )

    # queries_log — UX metriği. user_id yalnız X-Custos-User'dan gelen id'yi
    # saklar; public.users'a FK YOK (karar A — şemalar arası bağımlılık yok).
    op.execute(
        """
        CREATE TABLE assistant.queries_log (
            query_id          SERIAL PRIMARY KEY,
            query_text        TEXT,
            result_chunk_ids  INT[],
            selected_chunk_id INT,
            query_time_ms     INT,
            asked_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            user_id           INT
        );
        """
    )

    # custos_app DML yetkileri — yalnızca rol mevcutsa (prod two-user kurulum).
    # Dev tek-user'da custos_app yok → DO bloğu no-op (rollback drill yeşil kalır).
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'custos_app') THEN
                GRANT USAGE ON SCHEMA assistant TO custos_app;
                GRANT SELECT, INSERT, UPDATE, DELETE
                    ON ALL TABLES IN SCHEMA assistant TO custos_app;
                GRANT USAGE, SELECT
                    ON ALL SEQUENCES IN SCHEMA assistant TO custos_app;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    """`assistant` şemasını tüm nesneleriyle (tablo/index/sequence/grant) düşürür."""
    op.execute("DROP SCHEMA IF EXISTS assistant CASCADE;")
