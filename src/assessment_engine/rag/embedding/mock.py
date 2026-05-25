"""Mock embedding client — deterministic random vector.

ADR 0024 EMBEDDING_PROVIDER 의 기본값. 외부 호출 0, 비용 0.
입력 text 의 SHA-256 hash 를 seed 로 numpy random vector 생성 -> 같은 text 는 같은 vector.
검증·테스트용 — 실제 의미 유사도 보장 X (random 이라 무관 텍스트끼리 유사 가능).
"""

import hashlib
import random

from assessment_engine.rag.embedding.base import BaseEmbeddingClient


class MockEmbeddingClient(BaseEmbeddingClient):
    def __init__(self, dimension: int = 1024) -> None:
        self._dimension = dimension

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._deterministic_vector(t) for t in texts]

    def _deterministic_vector(self, text: str) -> list[float]:
        # SHA-256 hash -> int seed -> random vector. 같은 text -> 같은 vector 보장.
        seed = int(hashlib.sha256(text.encode("utf-8")).hexdigest(), 16) % (2**32)
        rng = random.Random(seed)
        # 단위 vector (cosine similarity 정합) — L2 norm 1.
        raw = [rng.gauss(0.0, 1.0) for _ in range(self._dimension)]
        norm = sum(x * x for x in raw) ** 0.5
        if norm == 0:
            return raw
        return [x / norm for x in raw]
