"""drop rag_documents + pgvector extension (ADR 0039)

Revision ID: e2f4a6c8b0d3
Revises: d5c8b1a3e9f2
Create Date: 2026-06-10 00:00:00.000000

ADR 0039: RAG 제거 (0024 supersede). 진단은 통계 집계 -> LLM 합성 단일 경로로 정리.
rag_documents 테이블 + pgvector extension 제거. vector 타입 의존 객체는 rag_documents 단독이라
extension DROP 안전.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e2f4a6c8b0d3"
down_revision: str | Sequence[str] | None = "d5c8b1a3e9f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 인덱스(ix_rag_documents_source_type · rag_documents_embedding_hnsw_idx)는 테이블 DROP 시 동반 제거.
    op.execute("DROP TABLE IF EXISTS rag_documents")
    # vector 타입 의존 객체가 rag_documents 단독 — extension 제거 안전.
    op.execute("DROP EXTENSION IF EXISTS vector")


def downgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

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

    op.execute("""
        CREATE INDEX rag_documents_embedding_hnsw_idx
        ON rag_documents
        USING hnsw (embedding vector_cosine_ops)
    """)
