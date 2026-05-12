"""진단 워커 entry — aio-pika 큐 소비 + composition root (ADR 0004).

기동: `python -m assessment_engine.diagnostic`.
consumer/main.py 패턴 그대로 — exchange·DLX·큐 인자는 web/main.py 및 consumer/main.py와 일치 (#B3).
prefetch_count=1로 LLM 호출 동시성을 1로 제한 — 단일 큐에서 LLM rate limit 자연 throttle.
"""
import asyncio

import aio_pika
from loguru import logger

from assessment_engine.config import diagnostic_settings
from assessment_engine.db.redis import close_pool, get_redis
from assessment_engine.db.repositories.diagnostic_repository import DiagnosticRepository
from assessment_engine.db.repositories.query_repository import QueryRepository
from assessment_engine.db.session import AsyncSessionLocal
from assessment_engine.diagnostic.handler import make_diagnostic_handler
from assessment_engine.diagnostic.llm.base import BaseLlmClient


def _build_llm_client() -> BaseLlmClient:
    """LLM 토글 (ADR 0004) — composition root. 과금 발생 외부 API 호출 금지."""
    provider = diagnostic_settings.llm_provider
    if provider == "mock":
        from assessment_engine.diagnostic.llm.mock import MockLlmClient
        return MockLlmClient()
    if provider == "ollama":
        # ollama 클라이언트는 별도 PR — 1차 mock 전용. 도입 시 본 분기 활성화.
        raise NotImplementedError("LLM_PROVIDER=ollama not implemented yet (Phase 2)")
    raise ValueError(f"unknown LLM_PROVIDER: {provider}")


async def main() -> None:
    logger.info("diagnostic worker starting provider={} exchange={}",
                diagnostic_settings.llm_provider,
                diagnostic_settings.rabbitmq_exchange)

    redis = get_redis()
    llm_client = _build_llm_client()
    handler = make_diagnostic_handler(
        session_factory=AsyncSessionLocal,
        query_repo_factory=QueryRepository,
        diagnostic_repo_factory=DiagnosticRepository,
        llm_client=llm_client,
        redis=redis,
    )

    dlx_name = f"{diagnostic_settings.rabbitmq_exchange}.dlx"
    routing_key = diagnostic_settings.diagnostic_routing_key

    try:
        conn = await aio_pika.connect_robust(diagnostic_settings.broker_url, timeout=10)
        async with conn:
            async with conn.channel() as channel:
                await channel.set_qos(prefetch_count=1)

                dlx = await channel.declare_exchange(
                    dlx_name, aio_pika.ExchangeType.DIRECT, durable=True,
                )
                exchange = await channel.declare_exchange(
                    diagnostic_settings.rabbitmq_exchange,
                    aio_pika.ExchangeType.DIRECT,
                    durable=True,
                )

                dlq = await channel.declare_queue(f"{routing_key}.dead", durable=True)
                await dlq.bind(dlx, routing_key=routing_key)

                queue = await channel.declare_queue(
                    routing_key,
                    durable=True,
                    arguments={
                        "x-dead-letter-exchange":    dlx_name,
                        "x-dead-letter-routing-key": routing_key,
                        "x-message-ttl":             diagnostic_settings.diagnostic_queue_ttl_ms,
                        "x-max-length":              diagnostic_settings.diagnostic_queue_max_len,
                    },
                )
                await queue.bind(exchange, routing_key=routing_key)
                await queue.consume(handler)
                logger.info("diagnostic worker ready — consuming queue={}", routing_key)

                await asyncio.Future()
    finally:
        await close_pool()
