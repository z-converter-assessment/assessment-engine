from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class RetrievedDoc:
    """RAG 검색 결과 1건 (ADR 0024).

    - content: 검색된 chunk 원문 (LLM prompt 안 인용 대상)
    - score: cosine similarity 점수 (1.0 정확 일치, 0.0 무관)
    - metadata: source 출처·tag·날짜 등 (예: {'source': 'use-method.md', 'chunk_index': 3, 'title': 'CPU saturation'})
    - source_id: rag_documents.source_id (file_path + chunk_index 합성, dedup 키)
    """

    content: str
    score: float
    metadata: dict[str, Any]
    source_id: str


class BaseRetriever(ABC):
    """RAG retriever 추상 (ADR 0024).

    query 텍스트 -> embedding -> vector DB 검색 -> top-k 유사 문서.
    composition root (worker main · web settings) 에서 구체 주입 (F4).
    """

    @abstractmethod
    async def retrieve(
        self,
        query: str,
        top_k: int,
        source_type: str,
    ) -> list[RetrievedDoc]:
        """query -> top_k 유사 문서.

        source_type 으로 자료 카탈로그 필터 (domain_knowledge / operation_note / peer_snapshot).
        """
