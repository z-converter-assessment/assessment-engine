"""진단 메시지 핸들러 — RabbitMQ 메시지 1건 = engineer 보고서 narrative job 1건 처리 (ADR 0004 + 0024).

engineer 보고서 발행(web `emit_report`)이 status=pending job + 정적 스냅샷을 저장하고 publish.
워커는 본 핸들러로 pending job 을 받아 narrative 를 합성, 스냅샷 result 에 merge 후 mark_succeeded.

narrative 단위:
- scope='server': input_params['server_public_ids'] N대 loop — extract_server 별 narrative N건 (key=public_id).
- scope='environment': extract_environment 1건 (key=ENV_NARRATIVE_KEY).

요구 정합: narrative 는 발행 시점에만 1회. 개별 narrative 실패는 entry.status='failed' 로 흡수 —
보고서 스냅샷 자체는 항상 표시 (요구: 발행 외 진단 금지·실패해도 발행 보존). DB 일시 장애만 DLQ 재시도.
RAG_ENABLED=False 시 retriever None (rag_context=[]).
"""

import asyncio
import dataclasses
import json
from collections.abc import Callable
from datetime import datetime

import aio_pika
import httpx
from loguru import logger
from redis.asyncio import Redis
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from assessment_engine.cache.redis import safe_set, safe_set_nx
from assessment_engine.db.repositories.base_diagnostic_repository import (
    CLASSIFICATION_LABEL_KR,
    DIAGNOSTIC_RANGE_DAYS,
    BaseDiagnosticRepository,
)
from assessment_engine.db.repositories.query.base_query_repository import BaseQueryRepository
from assessment_engine.diagnostic import aggregator
from assessment_engine.diagnostic.llm.base import BaseLlmClient
from assessment_engine.diagnostic.llm.verify import find_hallucinated_numbers
from assessment_engine.diagnostic.report_result import (
    ENV_NARRATIVE_KEY,
    NARRATIVE_STATUS_SUCCEEDED,
    build_narrative_entry,
)
from assessment_engine.diagnostic.settings import diagnostic_settings
from assessment_engine.rag.query import build_query
from assessment_engine.rag.retriever.base import BaseRetriever

_REPORT_JOB_TYPES = ("engineer_report",)


def make_diagnostic_handler(
    session_factory: async_sessionmaker[AsyncSession],
    query_repo_factory: Callable[[AsyncSession], BaseQueryRepository],
    diagnostic_repo_factory: Callable[[AsyncSession], BaseDiagnosticRepository],
    llm_client: BaseLlmClient,
    redis: Redis,
    retriever: BaseRetriever | None = None,
):
    """consumer handler 팩토리 패턴(F4) — composition root에서 의존성 주입.

    retriever 가 None 이면 RAG 비활성 (RAG_ENABLED=False default). composition root 가
    `diagnostic_settings.rag_enabled` 기준 분기 — True 시 PgVectorRetriever 주입, False 시 None.
    """

    async def handler(message: aio_pika.abc.AbstractIncomingMessage):
        async with message.process(requeue=False):
            try:
                body = json.loads(message.body)
                job_id = body["job_id"]
            except (json.JSONDecodeError, KeyError) as e:
                # 메시지 자체 결함 — silent ack + 로그 (DLQ 보낼 가치 X, 운영자 개입 의미 없음).
                # #F8 — body raw dump 금지. message_id·길이·예외 타입만 로깅.
                logger.error(
                    "invalid diagnostic message message_id={} body_size={} err_type={}",
                    message.message_id,
                    len(message.body or b""),
                    type(e).__name__,
                )
                return

            # 멱등성 1단 (fail-open) — 동일 message_id 중복 전송 차단. Redis 장애 시 통과.
            message_id = message.message_id or job_id
            ok = await safe_set_nx(
                redis,
                diagnostic_settings.redis_key_idempotent.format(message_id),
                "1",
                diagnostic_settings.redis_ttl_idempotent,
            )
            if ok is False:
                logger.info("duplicate diagnostic message dropped job_id={} message_id={}", job_id, message_id)
                return

            async with session_factory() as session:
                query_repo = query_repo_factory(session)
                diag_repo = diagnostic_repo_factory(session)

                job = await diag_repo.get_by_id(job_id)
                if job is None:
                    logger.warning("diagnostic job not found id={}", job_id)
                    return
                if job.job_type not in _REPORT_JOB_TYPES:
                    # AI 진단 독립 폐기 — engineer_report 외 job_type 은 발행 경로가 publish 하지 않음.
                    # 잔존 메시지 방어: silent ack (DLQ 의미 없음).
                    logger.info("diagnostic skip non-report job_id={} job_type={}", job_id, job.job_type)
                    return
                if job.status != "pending":
                    # 이미 처리 중·완료 — 멱등성 1단이 fail-open이라 가능
                    logger.info("diagnostic skip non-pending job_id={} status={}", job_id, job.status)
                    return

                try:
                    await diag_repo.mark_running(job_id, "generating_narrative")
                    await session.commit()
                    await _publish_progress(redis, diag_repo, job_id)

                    narratives = await _build_report_narratives(
                        query_repo,
                        llm_client,
                        retriever,
                        job.scope,
                        job.input_params,
                    )

                    # 발행 시점 스냅샷(result) 보존 + narrative merge — 정적 보관 유지.
                    result = dict(job.result or {})
                    result["narratives"] = narratives
                    result["narrative_status"] = NARRATIVE_STATUS_SUCCEEDED
                    await diag_repo.mark_succeeded(job_id, result)
                    await session.commit()
                    await _publish_progress(redis, diag_repo, job_id)

                    # N대 표 발행 시 함께 만든 개별 단일 보고서(child)에 server 별 narrative 복사 —
                    # 표·개별이 같은 생성 결과 공유 (단일 생성, 이력 조회 시 항상 일관, 재생성 없음).
                    await _copy_narratives_to_children(
                        diag_repo, redis, session, result.get("child_jobs") or {}, narratives
                    )

                    failed = sum(1 for e in narratives.values() if e.get("status") == "failed")
                    logger.info(
                        "report narrative done job_id={} scope={} narratives={} failed={} children={}",
                        job_id,
                        job.scope,
                        len(narratives),
                        failed,
                        len(result.get("child_jobs") or {}),
                    )
                except OperationalError:
                    # DB 일시 장애 — DLQ 재시도 기회 (F6 fail-close). job은 pending/running 유지.
                    logger.exception("report narrative db unavailable job_id={}", job_id)
                    raise
                except (KeyError, IntegrityError) as e:
                    # input_params 누락·UNIQUE 위반 등 영구 실패 — status='failed' 흡수.
                    # (개별 narrative 실패는 _build_report_narratives 안에서 entry.status 로 흡수)
                    logger.exception("report narrative failed job_id={}", job_id)
                    await diag_repo.mark_failed(job_id, str(e)[:500])
                    await session.commit()
                    await _publish_progress(redis, diag_repo, job_id)

    return handler


async def _copy_narratives_to_children(
    diag_repo: BaseDiagnosticRepository,
    redis: Redis,
    session: AsyncSession,
    child_jobs: dict,
    narratives: dict[str, dict],
) -> None:
    """N대 표의 server 별 narrative 를 개별 단일 보고서(child) job 에 복사 + mark_succeeded.

    표·개별이 동일 생성 결과를 공유 — 같은 server narrative 가 두 번 생성돼 달라지는 일 없음.
    child 는 발행 시 publish=False 로 pending 저장된 상태. 이미 종료된 child 는 skip (멱등).
    """
    if not child_jobs:
        return
    updated = False
    for pid, child_id in child_jobs.items():
        entry = narratives.get(pid)
        if entry is None:
            continue
        child = await diag_repo.get_by_id(child_id)
        if child is None or child.status != "pending":
            continue
        cres = dict(child.result or {})
        cres["narratives"] = {pid: entry}
        cres["narrative_status"] = NARRATIVE_STATUS_SUCCEEDED
        await diag_repo.mark_succeeded(child_id, cres)
        updated = True
    if updated:
        await session.commit()
        for child_id in child_jobs.values():
            await _publish_progress(redis, diag_repo, child_id)


async def _build_report_narratives(
    query_repo: BaseQueryRepository,
    llm_client: BaseLlmClient,
    retriever: BaseRetriever | None,
    scope: str,
    input_params: dict,
) -> dict[str, dict]:
    """scope 별 narrative dict 생성 — server N대 loop / environment 1건.

    개별 narrative 실패(LLM·집계 데이터 부족·환각)는 entry.status='failed' 로 흡수 — 한 대 실패가
    다른 대 narrative 를 막지 않음. DB 일시 장애(OperationalError)는 catch 안 함 -> DLQ 재시도.
    """
    time_range = input_params["time_range"]
    period_days = DIAGNOSTIC_RANGE_DAYS[time_range]
    end = datetime.fromisoformat(input_params["anchor_at"])

    narratives: dict[str, dict] = {}
    if scope == "environment":
        narratives[ENV_NARRATIVE_KEY] = await _narrative_for(
            query_repo, llm_client, retriever, "environment", None, period_days, end, time_range
        )
    else:
        for pid in input_params.get("server_public_ids") or []:
            narratives[pid] = await _narrative_for(
                query_repo, llm_client, retriever, "server", pid, period_days, end, time_range
            )
    return narratives


async def _narrative_for(
    query_repo: BaseQueryRepository,
    llm_client: BaseLlmClient,
    retriever: BaseRetriever | None,
    scope: str,
    server_public_id: str | None,
    period_days: float,
    end: datetime,
    time_range: str,
) -> dict:
    """단일 key narrative entry — 집계 payload -> RAG -> LLM 합성 + 환각 검증.

    실패(집계 데이터 부족 ValueError·LLM timeout/HTTP·환각 ValueError·payload KeyError)는
    entry.status='failed' 로 반환 — 보고서 스냅샷은 항상 표시.
    """
    try:
        if scope == "server":
            payload = await aggregator.extract_server(query_repo, server_public_id, period_days, end, time_range)
        else:
            payload = await aggregator.extract_environment(query_repo, period_days, end, time_range)

        payload["rag_context"] = await _retrieve_rag_context(retriever, scope, payload)
        narrative = await _generate_narrative_with_verification(llm_client, scope, payload)

        # server scope 는 단일 분류 라벨, environment 는 분포 dict 라 단일 라벨 없음 (None).
        cls = payload.get("classification")
        classification = cls if isinstance(cls, str) else None
        recommendation_action = (payload.get("recommendation") or {}).get("action")
        return build_narrative_entry(
            status="succeeded",
            narrative=narrative,
            classification=classification,
            classification_label_kr=CLASSIFICATION_LABEL_KR.get(classification) if classification else None,
            recommendation_action=recommendation_action,
        )
    except (TimeoutError, httpx.HTTPError, ValueError, KeyError) as e:
        # 집계 데이터 부족(ValueError)·LLM timeout/HTTP·환각(ValueError)·payload 키 누락(KeyError).
        # #F8 — 식별자·예외 타입만 (payload·URL 노출 금지).
        logger.warning(
            "report narrative entry failed scope={} key={} err_type={}",
            scope,
            server_public_id,
            type(e).__name__,
        )
        return build_narrative_entry(status="failed", error=type(e).__name__)


async def _generate_narrative_with_verification(
    llm_client: BaseLlmClient,
    scope: str,
    payload: dict,
) -> str:
    """LLM narrative 호출 + 수치 환각 검증 + 재생성 1회 (ADR 0003 3G절 + ADR 0025).

    1회차 narrative 환각 발견 시 재생성. 재생성도 실패 시 ValueError raise — 호출자(`_narrative_for`)
    가 entry.status='failed' 로 흡수.

    각 호출 timeout = llm_timeout_seconds (F6 외부 의존 timeout 의무).
    """
    hallucinated: set[str] = set()
    narrative = ""
    for attempt in (1, 2):
        narrative = await asyncio.wait_for(
            llm_client.generate_narrative(scope, payload),
            timeout=diagnostic_settings.llm_timeout_seconds,
        )
        hallucinated = find_hallucinated_numbers(narrative, payload)
        if not hallucinated:
            return narrative
        logger.warning(
            "llm hallucination detected attempt={} scope={} hallucinated={}",
            attempt,
            scope,
            sorted(hallucinated)[:5],
        )
    raise ValueError(f"llm_hallucination: {sorted(hallucinated)[:5]}")


async def _retrieve_rag_context(
    retriever: BaseRetriever | None,
    scope: str,
    payload: dict,
) -> list[dict]:
    """RAG 검색 (ADR 0024).

    retriever None 시 빈 list. 외부 호출 실패 시 silent fallback (RAG 결과 없어도 narrative 합성 가능).
    """
    if retriever is None:
        return []
    try:
        query = build_query(scope, payload)
        docs = await retriever.retrieve(
            query=query,
            top_k=diagnostic_settings.rag_top_k,
            source_type="domain_knowledge",
        )
    except (SQLAlchemyError, httpx.HTTPError, TimeoutError, ValueError) as e:
        # RAG 실패는 narrative 자체 실패 아님 — 합성으로 진행. 운영자 인지용 WARNING.
        # 본질 catch = DB query (SQLAlchemyError) · embedding HTTP (httpx.HTTPError)
        # · timeout · response shape (ValueError).
        logger.warning("rag retrieve failed scope={} err_type={}", scope, type(e).__name__)
        return []
    # rag_max_context_chars cap — LLM prompt 안 context 절 길이 제한.
    max_chars = diagnostic_settings.rag_max_context_chars
    out: list[dict] = []
    total = 0
    for doc in docs:
        remaining = max_chars - total
        if remaining <= 0:
            break
        content = doc.content[:remaining] if len(doc.content) > remaining else doc.content
        out.append(
            {
                "content": content,
                "score": doc.score,
                "metadata": doc.metadata,
                "source_id": doc.source_id,
            }
        )
        total += len(content)
    return out


async def _publish_progress(redis: Redis, diag_repo: BaseDiagnosticRepository, job_id: str) -> None:
    """단계 UPDATE 후 Redis polling 캐시 SET — web GET이 본 캐시 우선 read (#C3 fail-open)."""
    record = await diag_repo.get_by_id(job_id)
    if record is None:
        return
    await safe_set(
        redis,
        diagnostic_settings.redis_key_diagnostic_progress.format(job_id),
        _to_cache_blob(record),
        ex=diagnostic_settings.redis_ttl_diagnostic_progress,
    )


def _to_cache_blob(record) -> str:
    """DiagnosticJobRecord → JSON 문자열. datetime은 ISO 8601 UTC (web service가 fromisoformat 복원)."""
    data = dataclasses.asdict(record)
    for key in ("created_at", "started_at", "finished_at"):
        v = data.get(key)
        if v is not None and hasattr(v, "isoformat"):
            data[key] = v.isoformat()
    return json.dumps(data)
