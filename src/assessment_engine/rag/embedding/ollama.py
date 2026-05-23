"""Ollama embedding client — 로컬 무료 LLM (ADR 0024).

HTTP POST /api/embed (ollama 표준 API) — model + input (list[str]) -> embeddings list[list[float]].
default 모델 = `mxbai-embed-large` (1024 차원, Matryoshka, Apache 2.0, MTEB 영어 retrieval 상위).
과금 발생 외부 API 호출 금지 정책 정합 — 본 컴포넌트는 로컬 ollama 단독 호출.
"""

import asyncio
import time

import httpx
from loguru import logger

from assessment_engine.rag.embedding.base import BaseEmbeddingClient

# 일시 장애 (network · ollama 일시 unavailable · 5xx) retry 정공 (F6).
_RETRY_ATTEMPTS = 3
_RETRY_BACKOFF_BASE_S = 0.5


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

        # last_error 의도 = retry 모두 실패 시 마지막 예외 reraise. for loop 안 break 없이 종료 = 모든
        # attempt catch — last_error 항상 설정. dummy default 안 type 안 None 본 시점 정공 (F1 assert 회피).
        last_error: Exception = RuntimeError("retry exhausted with no error captured")
        for attempt in range(1, _RETRY_ATTEMPTS + 1):
            start = time.monotonic()
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(url, json=payload)
                    response.raise_for_status()
                    body = response.json()
                elapsed_ms = int((time.monotonic() - start) * 1000)
                embeddings = body.get("embeddings")
                if not isinstance(embeddings, list) or len(embeddings) != len(texts):
                    logger.error(
                        "ollama embed response shape mismatch expected={} got={}",
                        len(texts),
                        len(embeddings) if isinstance(embeddings, list) else "non-list",
                    )
                    raise ValueError("ollama embed response shape mismatch")
                # F7 observability — embedding latency + input count 기록.
                logger.info(
                    "ollama embed model={} input_count={} elapsed_ms={} attempt={}",
                    self._model,
                    len(texts),
                    elapsed_ms,
                    attempt,
                )
                return embeddings
            except httpx.HTTPError as e:
                last_error = e
                elapsed_ms = int((time.monotonic() - start) * 1000)
                logger.warning(
                    "ollama embed http retry attempt={} elapsed_ms={} err_type={}",
                    attempt,
                    elapsed_ms,
                    type(e).__name__,
                )
                if attempt < _RETRY_ATTEMPTS:
                    await asyncio.sleep(_RETRY_BACKOFF_BASE_S * attempt)
        raise last_error
