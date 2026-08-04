"""report_history mapper — DiagnosticJobRecord (job_type='customer_report'|'engineer_report')

→ 보고서 이력 row dict 변환 단위 테스트.

분기 풍부 (T13):
- view: customer_report → 'customer', engineer_report → 'engineer'
- scope: server (1대 vs N대 result_link 라우터 다름) vs environment — 모두 `?job={id}` 정적 스냅샷
- window_label: input_params.time_range 우선, 없으면 period_days fallback
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from assessment_engine.db.dtos.outbound import DiagnosticJobRecord
from assessment_engine.web.services.mappers.report_history import to_report_history_item

if TYPE_CHECKING:
    from assessment_engine.json_types import JsonObject


def _rec(
    *,
    job_type: str,
    scope: str,
    input_params: JsonObject,
    result: JsonObject | None = None,
) -> DiagnosticJobRecord:
    """DiagnosticJobRecord 최소 fixture — report_history 변환 검증용."""
    return DiagnosticJobRecord(
        id="00000000-0000-0000-0000-000000000001",
        job_type=job_type,
        scope=scope,
        input_params=input_params,
        input_hash="x" * 64,
        status="succeeded",
        progress_stage=None,
        result=result,
        error_message=None,
        created_at=datetime(2026, 5, 20, tzinfo=UTC),
        started_at=None,
        finished_at=None,
        requested_by=None,
    )


# ─── view·view_label 매핑 ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("job_type", "expected_view", "expected_label"),
    [
        ("customer_report", "customer", "고객 보고서"),
        ("engineer_report", "engineer", "엔지니어 보고서"),
        # unknown job_type → customer fallback (mapper 정책)
        ("ai_diagnostic", "customer", "고객 보고서"),
    ],
)
def test_view_label_from_job_type(job_type: str, expected_view: str, expected_label: str):
    rec = _rec(
        job_type=job_type,
        scope="server",
        input_params={"server_public_ids": ["pid-1"], "time_range": "14d", "period_days": 14},
    )
    item = to_report_history_item(rec)
    assert item["view"] == expected_view
    assert item["view_label"] == expected_label


# ─── result_link — 발행된 정적 스냅샷 ?job={id} (scope/서버 수에 따라 라우터만 분기) ─────


_JOB_ID = "00000000-0000-0000-0000-000000000001"


def test_server_scope_single_server_link():
    """server scope 1대 → /servers/{pid}/report?job={id} (단일 양식)."""
    rec = _rec(
        job_type="customer_report",
        scope="server",
        input_params={"server_public_ids": ["pid-only"], "time_range": "14d", "period_days": 14},
    )
    item = to_report_history_item(rec)
    assert item["result_link"] == f"/servers/pid-only/report?job={_JOB_ID}"
    assert item["server_count"] == 1


def test_server_scope_multi_server_link():
    """server scope N대 → /servers/report?job={id} (N대 표 양식)."""
    rec = _rec(
        job_type="engineer_report",
        scope="server",
        input_params={
            "server_public_ids": ["pid-a", "pid-b", "pid-c"],
            "time_range": "7d",
            "period_days": 7,
        },
    )
    item = to_report_history_item(rec)
    assert item["result_link"] == f"/reports/servers?job={_JOB_ID}"
    assert item["server_count"] == 3


def test_server_scope_zero_servers_link():
    """server_public_ids 없음 (희소) → /reports/servers?job={id}. server_count=0."""
    rec = _rec(
        job_type="customer_report",
        scope="server",
        input_params={"time_range": "14d", "period_days": 14},
    )
    item = to_report_history_item(rec)
    assert item["server_count"] == 0
    assert item["result_link"] == f"/reports/servers?job={_JOB_ID}"


def test_environment_scope_link():
    """environment scope → /reports/environment?job={id} (anchor/윈도우는 스냅샷에 보관)."""
    rec = _rec(
        job_type="customer_report",
        scope="environment",
        input_params={"time_range": "14d", "period_days": 14},
        result={"snapshot": {"base": {"total": 55}}},
    )
    item = to_report_history_item(rec)
    assert item["result_link"] == f"/reports/environment?job={_JOB_ID}"
    # environment 는 선택 list 부재 → 스냅샷 등록 서버 총수(base.total)로 표시.
    assert item["server_count"] == 55


def test_environment_scope_count_none_without_snapshot():
    """environment scope 인데 스냅샷 부재(pending/running/failed) → server_count None (표시 단계 수량 생략)."""
    rec = _rec(
        job_type="customer_report",
        scope="environment",
        input_params={"time_range": "14d", "period_days": 14},
        result={},
    )
    item = to_report_history_item(rec)
    assert item["server_count"] is None


def test_link_with_back_appends_referrer():
    """back 전달 시 ?job={id}&back={referrer} chain."""
    rec = _rec(
        job_type="engineer_report",
        scope="environment",
        input_params={"time_range": "30d"},
    )
    item = to_report_history_item(rec, back="%2Freports%2Fhistory")
    assert item["result_link"] == f"/reports/environment?job={_JOB_ID}&back=%2Freports%2Fhistory"


# ─── _window_label / _resolve_time_range 분기 ───────────────────────────


def test_window_label_uses_input_params_time_range():
    """input_params.time_range 가 우선 — period_days 무시."""
    rec = _rec(
        job_type="customer_report",
        scope="server",
        input_params={"server_public_ids": ["pid"], "time_range": "1h", "period_days": 14},
    )
    item = to_report_history_item(rec)
    # DIAGNOSTIC_RANGE_LABEL_KR["1h"] == "1시간"
    assert item["window_label"] == "1시간"


def test_window_label_fallback_to_period_days_integer():
    """input_params.time_range 없음 + result.time_range 없음 → period_days fallback (정수 일)."""
    rec = _rec(
        job_type="customer_report",
        scope="server",
        input_params={"server_public_ids": ["pid"], "period_days": 7},
        result={},
    )
    item = to_report_history_item(rec)
    assert item["window_label"] == "7일"


def test_window_label_fallback_to_period_days_fraction():
    """period_days < 1 → 그대로 표시 (1일 미만 윈도우 보존)."""
    rec = _rec(
        job_type="customer_report",
        scope="server",
        input_params={"server_public_ids": ["pid"], "period_days": 0.5},
        result={},
    )
    item = to_report_history_item(rec)
    assert item["window_label"] == "0.5일"


def test_window_label_prefers_input_over_result():
    """window_label — input_params.time_range 가 result.time_range 보다 우선."""
    rec = _rec(
        job_type="customer_report",
        scope="server",
        input_params={"server_public_ids": ["pid"], "time_range": "6h"},
        result={"time_range": "30d"},
    )
    item = to_report_history_item(rec)
    assert item["window_label"] == "6시간"


def test_window_label_falls_back_to_result_time_range():
    """input_params.time_range 없음 → result.time_range 사용 (옛 row 호환)."""
    rec = _rec(
        job_type="customer_report",
        scope="server",
        input_params={"server_public_ids": ["pid"]},
        result={"time_range": "24h"},
    )
    item = to_report_history_item(rec)
    assert item["window_label"] == "1일"


def test_window_label_default_when_both_missing():
    """time_range 없음 + period_days 없음 → period_days 기본 14 → "14일"."""
    rec = _rec(
        job_type="customer_report",
        scope="server",
        input_params={"server_public_ids": ["pid"]},
        result=None,
    )
    item = to_report_history_item(rec)
    assert item["window_label"] == "14일"


# ─── to_report_history_item 응답 dict shape ───────────────────────────


def test_to_report_history_item_returns_all_required_keys():
    """template (history.html) 이 attribute access 하는 8 키 모두 채움."""
    rec = _rec(
        job_type="engineer_report",
        scope="server",
        input_params={"server_public_ids": ["pid-1", "pid-2"], "time_range": "14d", "period_days": 14},
    )
    item = to_report_history_item(rec)
    expected = {"job_id", "scope", "view", "view_label", "server_count", "window_label", "created_at", "result_link"}
    assert set(item.keys()) == expected
    assert item["job_id"] == rec.id
    assert item["scope"] == "server"
    assert item["created_at"] == rec.created_at
