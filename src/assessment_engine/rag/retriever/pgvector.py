"""pgvector retriever — HNSW + cosine similarity (ADR 0024).

흐름:
1. query 텍스트 -> embedding client -> query_vec (1024-dim)
2. SQL: SELECT content, score, metadata, source_id FROM rag_documents
        WHERE source_type = :source_type
        ORDER BY embedding <=> :query_vec  -- pgvector cosine distance 연산자
        LIMIT :top_k
3. cosine similarity = 1 - cosine_distance 변환

`<=>` = pgvector cosine distance 연산자 (0 = 정확 일치, 2 = 정반대).
HNSW 인덱스 (vector_cosine_ops) 가 ORDER BY <=> + LIMIT 패턴에 활용 됨 (recall 95%+ 안정).
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from assessment_engine.rag.embedding.base import BaseEmbeddingClient
from assessment_engine.rag.retriever.base import BaseRetriever, RetrievedDoc


class PgVectorRetriever(BaseRetriever):
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        embedding_client: BaseEmbeddingClient,
    ) -> None:
        self._session_factory = session_factory
        self._embedding = embedding_client

    async def retrieve(
        self,
        query: str,
        top_k: int,
        source_type: str,
    ) -> list[RetrievedDoc]:
        if not query or top_k <= 0:
            return []

        query_vecs = await self._embedding.embed([query])
        if not query_vecs:
            return []
        query_vec = query_vecs[0]

        # pgvector 의 vector literal = '[0.1, 0.2, ...]' 문자열. asyncpg bind 시 cast 의무.
        query_vec_literal = "[" + ",".join(f"{x:.8f}" for x in query_vec) + "]"

        sql = text("""
            SELECT
                content,
                source_id,
                metadata,
                1 - (embedding <=> CAST(:query_vec AS vector)) AS score
            FROM rag_documents
            WHERE source_type = :source_type
            ORDER BY embedding <=> CAST(:query_vec AS vector)
            LIMIT :top_k
        """)
        async with self._session_factory() as session:
            result = await session.execute(
                sql,
                {
                    "query_vec": query_vec_literal,
                    "source_type": source_type,
                    "top_k": top_k,
                },
            )
            rows = result.all()

        return [
            RetrievedDoc(
                content=row.content,
                score=float(row.score),
                metadata=row.metadata or {},
                source_id=row.source_id,
            )
            for row in rows
        ]
