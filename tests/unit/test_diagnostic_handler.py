"""diagnostic handler — F6 fail-close/흡수 매트릭스 검증 (ADR 0004 정정 이력 2026-05-14).

핵심 규약:
- OperationalError → reraise → message.process(requeue=False)가 NACK → DLQ 재시도
- (ValueError, KeyError, IntegrityError) → mark_failed로 흡수 → message ack (사용자가 결과 페이지에서 인지)
- 그 외 광범위 Exception은 catch 안 함 (의도되지 않은 예외는 그대로 raise → DLQ)
"""

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import IntegrityError, OperationalError

from assessment_engine.db.dtos.outbound import DiagnosticJobRecord
from assessment_engine.diagnostic.handler import make_diagnostic_handler


def _make_message(job_id: str = "j1", message_id: str = "m1"):
    """aio_pika.message stub — body + message.process() async context manager."""
    msg = MagicMock()
    msg.body = f'{{"job_id": "{job_id}"}}'.encode()
    msg.message_id = message_id

    @asynccontextmanager
    async def _process(requeue: bool = True):
        yield None

    msg.process = _process
    return msg


def _make_pending_job(job_id: str = "j1") -> DiagnosticJobRecord:
    return DiagnosticJobRecord(
        id=job_id,
        job_type="ai_diagnostic",
        scope="server",
        input_params={
            "server_public_id": "uuid-x",
            "time_range": "14d",
            "anchor_at": "2026-05-12T00:00:00+00:00",
        },
        input_hash="h",
        status="pending",
        progress_stage="queued",
        result=None,
        error_message=None,
        created_at=datetime(2026, 5, 12, tzinfo=UTC),
        started_at=None,
        finished_at=None,
        requested_by=None,
    )


@pytest.fixture
def stub_components():
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    session.commit = AsyncMock()
    session_factory = MagicMock(return_value=session)

    query_repo = AsyncMock()
    diag_repo = AsyncMock()
    diag_repo.get_by_id = AsyncMock(return_value=_make_pending_job())
    diag_repo.mark_running = AsyncMock()
    diag_repo.update_progress_stage = AsyncMock()
    diag_repo.mark_succeeded = AsyncMock()
    diag_repo.mark_failed = AsyncMock()

    llm = AsyncMock()
    llm.generate_narrative = AsyncMock(return_value="narrative")

    redis = AsyncMock()
    # safe_set_nx → True (멱등 1단 통과), safe_set → None
    return session_factory, query_repo, diag_repo, llm, redis


def _build_handler(stub_components):
    session_factory, query_repo, diag_repo, llm, redis = stub_components
    return make_diagnostic_handler(
        session_factory=session_factory,
        query_repo_factory=lambda s: query_repo,
        diagnostic_repo_factory=lambda s: diag_repo,
        llm_client=llm,
        redis=redis,
    ), diag_repo


@pytest.mark.asyncio
@patch("assessment_engine.diagnostic.handler.safe_set_nx", new=AsyncMock(return_value=True))
@patch("assessment_engine.diagnostic.handler.safe_set", new=AsyncMock(return_value=None))
@patch("assessment_engine.diagnostic.handler.aggregator")
async def test_handler_op_error_reraises_for_dlq(mock_agg, stub_components):
    """DB 일시 장애(OperationalError)는 reraise → message.process가 NACK → DLQ로."""
    mock_agg.extract_server = AsyncMock(side_effect=OperationalError("stmt", {}, Exception("connection lost")))
    handler, diag_repo = _build_handler(stub_components)
    msg = _make_message()
    with pytest.raises(OperationalError):
        await handler(msg)
    # mark_failed 호출 안 됨 — job은 pending 유지, DLQ에서 재시도 기회
    diag_repo.mark_failed.assert_not_called()


@pytest.mark.asyncio
@patch("assessment_engine.diagnostic.handler.safe_set_nx", new=AsyncMock(return_value=True))
@patch("assessment_engine.diagnostic.handler.safe_set", new=AsyncMock(return_value=None))
@patch("assessment_engine.diagnostic.handler.aggregator")
async def test_handler_value_error_absorbs_as_failed(mock_agg, stub_components):
    """aggregator no metrics ValueError → mark_failed 흡수 → ack."""
    mock_agg.extract_server = AsyncMock(side_effect=ValueError("no metrics for server"))
    handler, diag_repo = _build_handler(stub_components)
    await handler(_make_message())  # reraise 없음
    diag_repo.mark_failed.assert_awaited_once()
    err_msg = diag_repo.mark_failed.await_args.args[1]
    assert "no metrics" in err_msg


@pytest.mark.asyncio
@patch("assessment_engine.diagnostic.handler.safe_set_nx", new=AsyncMock(return_value=True))
@patch("assessment_engine.diagnostic.handler.safe_set", new=AsyncMock(return_value=None))
@patch("assessment_engine.diagnostic.handler.aggregator")
async def test_handler_key_error_absorbs_as_failed(mock_agg, stub_components):
    """input_params 키 누락(KeyError) → mark_failed 흡수."""
    mock_agg.extract_server = AsyncMock(side_effect=KeyError("server_public_id"))
    handler, diag_repo = _build_handler(stub_components)
    await handler(_make_message())
    diag_repo.mark_failed.assert_awaited_once()


@pytest.mark.asyncio
@patch("assessment_engine.diagnostic.handler.safe_set_nx", new=AsyncMock(return_value=True))
@patch("assessment_engine.diagnostic.handler.safe_set", new=AsyncMock(return_value=None))
@patch("assessment_engine.diagnostic.handler.aggregator")
async def test_handler_integrity_error_absorbs_as_failed(mock_agg, stub_components):
    """DB UNIQUE 충돌(IntegrityError) → mark_failed 흡수 (영구 오류 = 재시도 의미 없음)."""
    mock_agg.extract_server = AsyncMock(side_effect=IntegrityError("stmt", {}, Exception("duplicate key")))
    handler, diag_repo = _build_handler(stub_components)
    await handler(_make_message())
    diag_repo.mark_failed.assert_awaited_once()


@pytest.mark.asyncio
@patch("assessment_engine.diagnostic.handler.safe_set_nx", new=AsyncMock(return_value=False))
async def test_handler_idempotency_first_drops_duplicate(stub_components):
    """동일 message_id 재전송은 멱등성 1단(safe_set_nx False)이 silent drop."""
    handler, diag_repo = _build_handler(stub_components)
    await handler(_make_message())
    # 멱등성 1단 차단으로 get_by_id 호출 안 됨
    diag_repo.get_by_id.assert_not_called()


@pytest.mark.asyncio
@patch("assessment_engine.diagnostic.handler.safe_set_nx", new=AsyncMock(return_value=True))
async def test_handler_invalid_message_silent_ack(stub_components):
    """JSON 파싱 실패 메시지 — silent ack + ERROR 로그 (DLQ 보내봐야 운영자 개입 의미 없음)."""
    handler, diag_repo = _build_handler(stub_components)
    bad_msg = MagicMock()
    bad_msg.body = b"not json"
    bad_msg.message_id = "m-bad"

    @asynccontextmanager
    async def _process(requeue: bool = True):
        yield None

    bad_msg.process = _process

    await handler(bad_msg)  # reraise 없음
    diag_repo.get_by_id.assert_not_called()


# ─── LLM timeout 분기 (#F6) — wait_for(timeout=llm_timeout_seconds) ────────


@pytest.mark.asyncio
@patch("assessment_engine.diagnostic.handler.safe_set_nx", new=AsyncMock(return_value=True))
@patch("assessment_engine.diagnostic.handler.safe_set", new=AsyncMock(return_value=None))
@patch("assessment_engine.diagnostic.handler.aggregator")
async def test_handler_llm_timeout_marks_failed(mock_agg, stub_components):
    """LLM 호출 TimeoutError → mark_failed("llm_timeout") 흡수 (DLQ 재시도 안 함)."""
    mock_agg.extract_server = AsyncMock(return_value={"summary": "ok"})

    session_factory, query_repo, diag_repo, llm, redis = stub_components
    llm.generate_narrative = AsyncMock(side_effect=TimeoutError())

    handler = make_diagnostic_handler(
        session_factory=session_factory,
        query_repo_factory=lambda s: query_repo,
        diagnostic_repo_factory=lambda s: diag_repo,
        llm_client=llm,
        redis=redis,
    )
    await handler(_make_message())  # reraise 없음 (흡수)
    diag_repo.mark_failed.assert_awaited_once()
    args = diag_repo.mark_failed.await_args
    assert args.args[1] == "llm_timeout" or args.kwargs.get("error_message") == "llm_timeout"
    # succeeded 마킹은 안 함
    diag_repo.mark_succeeded.assert_not_called()
