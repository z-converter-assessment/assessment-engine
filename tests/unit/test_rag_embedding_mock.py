"""rag.embedding.mock — MockEmbeddingClient deterministic vector 단위 테스트 (ADR 0024).

본질 catalog:
- 같은 text -> 같은 vector (SHA-256 seed 보장)
- 다른 text -> 다른 vector
- L2 norm = 1 (cosine similarity 단위 vector 정합)
- dimension 인자 본질 (1024 default + custom)
- 빈 list 입력 안전성
"""

import math

import pytest

from assessment_engine.rag.embedding.mock import MockEmbeddingClient


@pytest.mark.asyncio
async def test_mock_same_text_yields_same_vector():
    """SHA-256 seed -> deterministic — 같은 text 반복 호출 시 동일 vector."""
    client = MockEmbeddingClient(dimension=64)
    v1 = (await client.embed(["hello"]))[0]
    v2 = (await client.embed(["hello"]))[0]
    assert v1 == v2


@pytest.mark.asyncio
async def test_mock_different_text_yields_different_vector():
    client = MockEmbeddingClient(dimension=64)
    v_a = (await client.embed(["alpha"]))[0]
    v_b = (await client.embed(["beta"]))[0]
    assert v_a != v_b


@pytest.mark.asyncio
async def test_mock_vector_is_unit_norm():
    """L2 norm = 1 (cosine similarity 정합 — 단위 vector 의무)."""
    client = MockEmbeddingClient(dimension=128)
    vectors = await client.embed(["test text"])
    assert len(vectors) == 1
    norm = math.sqrt(sum(x * x for x in vectors[0]))
    assert abs(norm - 1.0) < 1e-9


@pytest.mark.asyncio
async def test_mock_dimension_respected():
    client = MockEmbeddingClient(dimension=256)
    vectors = await client.embed(["a", "b", "c"])
    assert len(vectors) == 3
    for v in vectors:
        assert len(v) == 256


@pytest.mark.asyncio
async def test_mock_default_dimension_1024():
    client = MockEmbeddingClient()
    vectors = await client.embed(["any"])
    assert len(vectors[0]) == 1024


@pytest.mark.asyncio
async def test_mock_empty_input_yields_empty_output():
    client = MockEmbeddingClient(dimension=32)
    vectors = await client.embed([])
    assert vectors == []


@pytest.mark.asyncio
async def test_mock_batch_preserves_order():
    """batch 호출 결과 순서 = 입력 순서."""
    client = MockEmbeddingClient(dimension=64)
    texts = ["first", "second", "third"]
    vectors = await client.embed(texts)
    # 같은 text 단독 호출과 비교
    for text, vec in zip(texts, vectors, strict=True):
        single = (await client.embed([text]))[0]
        assert vec == single
