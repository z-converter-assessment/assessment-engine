from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Index, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from assessment_engine.db.models.base import Base


class RagDocument(Base):
    """RAG 문서 chunk — embedding 검색 대상 (ADR 0024).

    source_type 카탈로그:
    - 'domain_knowledge' (본 phase 1): USE Method · AWS Compute Optimizer · Brendan Gregg 등 외부 백서 chunk
    - 'operation_note' (보류): 운영자 수동 입력 메모·인시던트 이력
    - 'peer_snapshot' (보류): 본 환경 N대 서버 통계 snapshot vector

    source_id = file_path + chunk_index (또는 외부 자료 식별자) — UPSERT 키.
    백서 갱신 시 같은 source_id 로 재 insert.

    embedding 차원 = 1024 (mxbai-embed-large default, ADR 0024 결정 2).
    모델 변경 시 alembic migration 1회 (column 타입 변경 + 전체 자료 재 embedding).

    HNSW 인덱스 = `embedding vector_cosine_ops` — recall 95%+ 안정,
    ORDER BY <=> + LIMIT 패턴에 활용.

    embedding 컬럼 = `vector(1024)` (pgvector). ORM 안 String 으로 선언 — vector 타입
    SQLAlchemy 통합 제한 회피. 실제 read/write 는 raw SQL 단독 (`CAST(... AS vector)` 명시).
    """

    __tablename__ = "rag_documents"
    __table_args__ = (
        UniqueConstraint("source_id", name="uq_rag_documents_source_id"),
        Index("ix_rag_documents_source_type", "source_type"),
        # HNSW 인덱스는 alembic revision 안 직접 DDL (op.execute) 로 생성 — SQLAlchemy 표준 Index 가 USING hnsw 미지원.
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[str] = mapped_column(String(512), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB)
    # embedding vector(1024) — pgvector 타입. ORM 안 String placeholder (SQLAlchemy vector type 통합 회피).
    # 실제 read/write 는 raw SQL (PgVectorRetriever · ingest CLI) 단독 — ORM session.add() 활용 X.
    embedding: Mapped[str] = mapped_column("embedding", Text, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
