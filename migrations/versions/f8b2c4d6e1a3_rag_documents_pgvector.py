"""rag_documents + pgvector extension (ADR 0024)

Revision ID: f8b2c4d6e1a3
Revises: d4e6f8a0b1c3
Create Date: 2026-05-24 00:00:00.000000

본 revision 산출물:
1. pgvector extension 활성화 (postgres `CREATE EXTENSION IF NOT EXISTS vector`)
2. rag_documents 테이블 신규 — id PK · source_type · source_id UNIQUE · content · metadata jsonb · embedding vector(1024) · created_at · updated_at
3. HNSW 인덱스 (vector_cosine_ops) — recall 95%+ 안정, ORDER BY <=> + LIMIT 패턴 가속

ADR 0024 결정:
- embedding 모델 = mxbai-embed-large-v1 (1024 차원, Matryoshka, Apache 2.0)
- vector DB = pgvector (postgres extension)
- 인덱스 = HNSW + vector_cosine_ops + 기본 파라미터 (m=16, ef_construction=64)
- 본 phase 자료 카탈로그 = domain_knowledge 만 (operation_note · peer_snapshot 보류)
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f8b2c4d6e1a3"
down_revision: str | Sequence[str] | None = "d4e6f8a0b1c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # pgvector extension — postgres 안 vector 타입·연산자·인덱스 등록.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # rag_documents 테이블 — SQLAlchemy 표준 DDL 한정 (vector 타입은 raw SQL 보강).
    op.execute("""
        CREATE TABLE rag_documents (
            id BIGSERIAL PRIMARY KEY,
            source_type VARCHAR(32) NOT NULL,
            source_id VARCHAR(512) NOT NULL,
            content TEXT NOT NULL,
            metadata JSONB,
            embedding vector(1024) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_rag_documents_source_id UNIQUE (source_id)
        )
    """)

    op.create_index("ix_rag_documents_source_type", "rag_documents", ["source_type"])

    # HNSW 인덱스 — cosine similarity 검색 가속. 기본 파라미터 (m=16, ef_construction=64).
    op.execute("""
        CREATE INDEX rag_documents_embedding_hnsw_idx
        ON rag_documents
        USING hnsw (embedding vector_cosine_ops)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS rag_documents_embedding_hnsw_idx")
    op.drop_index("ix_rag_documents_source_type", table_name="rag_documents")
    op.execute("DROP TABLE IF EXISTS rag_documents")
    # pgvector extension 은 DROP 안 함 — 다른 테이블이 의존할 수 있음 (운영자 명시 시점 정리).
