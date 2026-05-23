from abc import ABC, abstractmethod


class BaseEmbeddingClient(ABC):
    """Embedding 클라이언트 추상 (ADR 0024).

    텍스트 -> 고차원 vector (1024 차원, mxbai-embed-large default) 변환. cosine similarity 비교용.
    composition root (worker main · ingest CLI) 에서 `EMBEDDING_PROVIDER` 에 따라 구체 주입 (F4).
    """

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """N 텍스트 -> N vector. batch 호출 정합 (ingest CLI 다수 chunk 동시 처리)."""
