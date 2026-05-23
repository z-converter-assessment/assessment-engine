"""Ollama embedding client — 로컬 무료 LLM (ADR 0024).

HTTP POST /api/embed (ollama 표준 API) — model + input (list[str]) -> embeddings list[list[float]].
default 모델 = `mxbai-embed-large` (1024 차원, Matryoshka, Apache 2.0, MTEB 영어 retrieval 상위).
과금 발생 외부 API 호출 금지 정책 정합 — 본 컴포넌트는 로컬 ollama 단독 호출.
"""

import httpx
from loguru import logger

from assessment_engine.rag.embedding.base import BaseEmbeddingClient


class OllamaEmbeddingClient(BaseEmbeddingClient):
    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "mxbai-embed-large",
        timeout_seconds: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = httpx.Timeout(timeout_seconds)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        url = f"{self._base_url}/api/embed"
        payload = {"model": self._model, "input": texts}
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            body = response.json()

        embeddings = body.get("embeddings")
        if not isinstance(embeddings, list) or len(embeddings) != len(texts):
            logger.error(
                "ollama embed response shape mismatch expected={} got={}",
                len(texts),
                len(embeddings) if isinstance(embeddings, list) else "non-list",
            )
            raise ValueError("ollama embed response shape mismatch")
        return embeddings
