"""diagnostic handler — engineer 보고서 narrative job 처리 + F6 fail-close/흡수 매트릭스 (ADR 0004 + 0024).

핵심 규약 (보고서 narrative 통합 후):
- engineer_report job 만 처리 — 그 외 job_type 은 silent ack (AI 진단 독립 폐기).
- 개별 narrative 실패(LLM timeout/HTTP·집계 ValueError·환각)는 entry.status='failed' 로 흡수 →
  보고서 스냅샷은 항상 mark_succeeded (요구: 발행 외 진단 금지·실패해도 발행 보존).
- OperationalError → reraise → message.process(requeue=False) NACK → DLQ 재시도.
- (KeyError, IntegrityError) job 레벨 → mark_failed 흡수.
"""

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import OperationalError

from assessment_engine.db.dtos.outbound import DiagnosticJobRecord
from assessment_engine.diagnostic.handler import make_diagnostic_handler
from assessment_engine.diagnostic.report_result import REPORT_KIND_ENV


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


def _make_pending_job(job_id: str = "j1", job_type: str = "engineer_report") -> DiagnosticJobRecord:
    """발행 시점 스냅샷이 저장된 pending engineer_report job (server scope 1대)."""
    return DiagnosticJobRecord(
        id=job_id,
        job_type=job_type,
        scope="server",
        input_params={
            "server_public_ids": ["uuid-x"],
            "server_public_id": "uuid-x",
            "time_range": "14d",
            "anchor_at": "2026-05-12T00:00:00+00:00",
        },
        input_hash="h",
        status="pending",
        progress_stage="queued",
        result={
            "kind": REPORT_KIND_ENV,
            "snapshot": {"base": {"rows": []}},
            "view": "engineer",
            "narrative_status": "pending",
            "narratives": {},
            "aux": {},
        },
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
    diag_repo.mark_succeeded = AsyncMock()
    diag_repo.mark_failed = AsyncMock()

    llm = AsyncMock()
    # 숫자 없는 narrative — find_hallucinated_numbers 환각 0.
    llm.generate_narrative = AsyncMock(return_value="요약 narrative")

    redis = AsyncMock()
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
async def test_handler_success_merges_narrative_into_snapshot(mock_agg, stub_components):
    """성공 — narratives[pid] succeeded entry + snapshot 보존 + narrative_status=succeeded."""
    mock_agg.extract_server = AsyncMock(
        return_value={"classification": "optimal", "recommendation": {"action": "no_action"}}
    )
    handler, diag_repo = _build_handler(stub_components)
    await handler(_make_message())
    diag_repo.mark_succeeded.assert_awaited_once()
    result_arg = diag_repo.mark_succeeded.await_args.args[1]
    assert result_arg["narrative_status"] == "succeeded"
    assert result_arg["snapshot"] == {"base": {"rows": []}}  # 발행 시점 스냅샷 보존
    entry = result_arg["narratives"]["uuid-x"]
    assert entry["status"] == "succeeded"
    assert entry["narrative"] == "요약 narrative"
    assert entry["classification"] == "optimal"
    diag_repo.mark_failed.assert_not_called()


@pytest.mark.asyncio
@patch("assessment_engine.diagnostic.handler.safe_set_nx", new=AsyncMock(return_value=True))
@patch("assessment_engine.diagnostic.handler.safe_set", new=AsyncMock(return_value=None))
@patch("assessment_engine.diagnostic.handler.aggregator")
async def test_handler_op_error_reraises_for_dlq(mock_agg, stub_components):
    """DB 일시 장애(OperationalError)는 per-key catch 안 됨 → reraise → DLQ."""
    mock_agg.extract_server = AsyncMock(side_effect=OperationalError("stmt", {}, Exception("connection lost")))
    handler, diag_repo = _build_handler(stub_components)
    with pytest.raises(OperationalError):
        await handler(_make_message())
    diag_repo.mark_failed.assert_not_called()
    diag_repo.mark_succeeded.assert_not_called()


@pytest.mark.asyncio
@patch("assessment_engine.diagnostic.handler.safe_set_nx", new=AsyncMock(return_value=True))
@patch("assessment_engine.diagnostic.handler.safe_set", new=AsyncMock(return_value=None))
@patch("assessment_engine.diagnostic.handler.aggregator")
async def test_handler_value_error_absorbs_as_failed_entry(mock_agg, stub_components):
    """집계 데이터 부족(ValueError)은 per-key 흡수 — entry.status=failed, 보고서는 mark_succeeded."""
    mock_agg.extract_server = AsyncMock(side_effect=ValueError("no metrics for server"))
    handler, diag_repo = _build_handler(stub_components)
    await handler(_make_message())
    diag_repo.mark_succeeded.assert_awaited_once()
    entry = diag_repo.mark_succeeded.await_args.args[1]["narratives"]["uuid-x"]
    assert entry["status"] == "failed"
    assert entry["error"] == "ValueError"
    diag_repo.mark_failed.assert_not_called()


@pytest.mark.asyncio
@patch("assessment_engine.diagnostic.handler.safe_set_nx", new=AsyncMock(return_value=True))
@patch("assessment_engine.diagnostic.handler.safe_set", new=AsyncMock(return_value=None))
@patch("assessment_engine.diagnostic.handler.aggregator")
async def test_handler_llm_timeout_absorbs_as_failed_entry(mock_agg, stub_components):
    """LLM TimeoutError 은 per-key 흡수 — entry failed, 보고서는 mark_succeeded (mark_failed 없음)."""
    mock_agg.extract_server = AsyncMock(return_value={})
    session_factory, query_repo, diag_repo, llm, redis = stub_components
    llm.generate_narrative = AsyncMock(side_effect=TimeoutError())
    handler, diag_repo = _build_handler(stub_components)
    await handler(_make_message())
    diag_repo.mark_succeeded.assert_awaited_once()
    entry = diag_repo.mark_succeeded.await_args.args[1]["narratives"]["uuid-x"]
    assert entry["status"] == "failed"
    assert entry["error"] == "TimeoutError"
    diag_repo.mark_failed.assert_not_called()


@pytest.mark.asyncio
@patch("assessment_engine.diagnostic.handler.safe_set_nx", new=AsyncMock(return_value=True))
@patch("assessment_engine.diagnostic.handler.safe_set", new=AsyncMock(return_value=None))
@patch("assessment_engine.diagnostic.handler.aggregator")
async def test_handler_llm_http_error_absorbs_as_failed_entry(mock_agg, stub_components):
    """ollama 미연결(httpx.HTTPError) per-key 흡수 — entry failed (#F8: err_type 만)."""
    import httpx

    mock_agg.extract_server = AsyncMock(return_value={})
    session_factory, query_repo, diag_repo, llm, redis = stub_components
    llm.generate_narrative = AsyncMock(side_effect=httpx.ConnectError("connection refused to 10.0.0.5:11434"))
    handler, diag_repo = _build_handler(stub_components)
    await handler(_make_message())
    entry = diag_repo.mark_succeeded.await_args.args[1]["narratives"]["uuid-x"]
    assert entry["status"] == "failed"
    assert entry["error"] == "ConnectError"
    assert "10.0.0.5" not in (entry["error"] or "")  # #F8 — URL 미노출
    diag_repo.mark_failed.assert_not_called()


@pytest.mark.asyncio
@patch("assessment_engine.diagnostic.handler.safe_set_nx", new=AsyncMock(return_value=True))
@patch("assessment_engine.diagnostic.handler.safe_set", new=AsyncMock(return_value=None))
async def test_handler_skips_non_report_job_type(stub_components):
    """engineer_report 외 job_type (ai_diagnostic 잔존 메시지) → silent ack, 처리 없음."""
    session_factory, query_repo, diag_repo, llm, redis = stub_components
    diag_repo.get_by_id = AsyncMock(return_value=_make_pending_job(job_type="ai_diagnostic"))
    handler, diag_repo = _build_handler(stub_components)
    await handler(_make_message())
    diag_repo.mark_running.assert_not_called()
    diag_repo.mark_succeeded.assert_not_called()
    diag_repo.mark_failed.assert_not_called()


@pytest.mark.asyncio
@patch("assessment_engine.diagnostic.handler.safe_set_nx", new=AsyncMock(return_value=False))
async def test_handler_idempotency_first_drops_duplicate(stub_components):
    """동일 message_id 재전송은 멱등성 1단(safe_set_nx False)이 silent drop."""
    handler, diag_repo = _build_handler(stub_components)
    await handler(_make_message())
    diag_repo.get_by_id.assert_not_called()


@pytest.mark.asyncio
@patch("assessment_engine.diagnostic.handler.safe_set_nx", new=AsyncMock(return_value=True))
async def test_handler_invalid_message_silent_ack(stub_components):
    """JSON 파싱 실패 메시지 — silent ack + ERROR 로그."""
    handler, diag_repo = _build_handler(stub_components)
    bad_msg = MagicMock()
    bad_msg.body = b"not json"
    bad_msg.message_id = "m-bad"

    @asynccontextmanager
    async def _process(requeue: bool = True):
        yield None

    bad_msg.process = _process
    await handler(bad_msg)
    diag_repo.get_by_id.assert_not_called()
