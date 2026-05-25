"""report_history mapper — DiagnosticJobRecord (job_type='customer_report'|'engineer_report')
→ 보고서 이력 row dict 변환 단위 테스트.

분기 풍부 (T13):
- view: customer_report → 'customer', engineer_report → 'engineer'
- scope: server (1대 vs N대 result_link 다름) vs environment (anchor_at 인코딩)
- window_label: input_params.time_range 우선, 없으면 period_days fallback
"""

from datetime import UTC, datetime

import pytest

from assessment_engine.db.dtos.outbound import DiagnosticJobRecord
from assessment_engine.web.services.mappers.report_history import to_report_history_item


def _rec(
    *,
    job_type: str,
    scope: str,
    input_params: dict,
    result: dict | None = None,
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
    "job_type, expected_view, expected_label",
    [
        ("customer_report", "customer", "고객 보고서"),
        ("engineer_report", "engineer", "엔지니어 보고서"),
        # unknown job_type → customer fallback (mapper 정책)
        ("ai_diagnostic", "customer", "고객 보고서"),
    ],
)
def test_view_label_from_job_type(job_type, expected_view, expected_label):
    rec = _rec(
        job_type=job_type,
        scope="server",
        input_params={"server_public_ids": ["pid-1"], "time_range": "14d", "period_days": 14},
    )
    item = to_report_history_item(rec)
    assert item["view"] == expected_view
    assert item["view_label"] == expected_label


# ─── server scope result_link — 1대 vs N대 분기 ─────────────────────────


def test_server_scope_single_server_link():
    """server scope 1대 → /servers/{id}/report?view=...&time_range=... (단일 detail URL)."""
    rec = _rec(
        job_type="customer_report",
        scope="server",
        input_params={"server_public_ids": ["pid-only"], "time_range": "14d", "period_days": 14},
    )
    item = to_report_history_item(rec)
    assert item["result_link"] == "/servers/pid-only/report?view=customer&time_range=14d"
    assert item["server_count"] == 1


def test_server_scope_multi_server_link():
    """server scope N대 → /servers/report?ids=...&view=...&time_range=... (다중 N대 양식)."""
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
    assert item["result_link"] == "/servers/report?ids=pid-a,pid-b,pid-c&view=engineer&time_range=7d"
    assert item["server_count"] == 3


def test_server_scope_zero_servers_link_no_ids():
    """server_public_ids 없음 (희소 케이스) → ids 빈 string. server_count=0."""
    rec = _rec(
        job_type="customer_report",
        scope="server",
        input_params={"time_range": "14d", "period_days": 14},
    )
    item = to_report_history_item(rec)
    assert item["server_count"] == 0
    assert item["result_link"] == "/servers/report?ids=&view=customer&time_range=14d"


# ─── environment scope result_link — anchor_at 인코딩 ────────────────────


def test_environment_scope_link_without_anchor():
    """environment scope + anchor_at 미존재 → view + time_range 만 query 에 포함."""
    rec = _rec(
        job_type="customer_report",
        scope="environment",
        input_params={"time_range": "14d", "period_days": 14},
        result={},
    )
    item = to_report_history_item(rec)
    assert item["result_link"] == "/reports/environment?view=customer&time_range=14d"
    assert item["server_count"] == 0  # environment scope 는 server_public_ids 없음


def test_environment_scope_link_with_anchor_encodes_plus_sign():
    """environment scope + anchor_at UTC offset (`+00:00`) → URL safe `%2B00%3A00` 인코딩.

    URL `+` 가 space 로 해석되어 라우터 422 발생 회귀 방지 (mapper._result_link 안 quote(safe="")).
    """
    rec = _rec(
        job_type="engineer_report",
        scope="environment",
        input_params={"time_range": "30d", "period_days": 30},
        result={"anchor_at": "2026-05-20T12:00:00+00:00"},
    )
    item = to_report_history_item(rec)
    # `+` → `%2B`, `:` → `%3A` (safe="" 라 모두 인코딩).
    assert "anchor_at=2026-05-20T12%3A00%3A00%2B00%3A00" in item["result_link"]
    assert item["result_link"].startswith("/reports/environment?view=engineer&time_range=30d&anchor_at=")


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


def test_resolve_time_range_prefers_input_over_result():
    """input_params.time_range 가 result.time_range 보다 우선."""
    rec = _rec(
        job_type="customer_report",
        scope="server",
        input_params={"server_public_ids": ["pid"], "time_range": "6h"},
        result={"time_range": "30d"},
    )
    item = to_report_history_item(rec)
    # result_link 의 time_range 는 input_params 우선.
    assert "time_range=6h" in item["result_link"]


def test_resolve_time_range_falls_back_to_result():
    """input_params.time_range 없음 → result.time_range 사용 (옛 row 호환)."""
    rec = _rec(
        job_type="customer_report",
        scope="server",
        input_params={"server_public_ids": ["pid"]},
        result={"time_range": "24h"},
    )
    item = to_report_history_item(rec)
    assert "time_range=24h" in item["result_link"]


def test_resolve_time_range_default_14d_when_both_missing():
    """input_params·result 모두 time_range 없음 → "14d" 기본값 (mapper 정책)."""
    rec = _rec(
        job_type="customer_report",
        scope="server",
        input_params={"server_public_ids": ["pid"]},
        result=None,
    )
    item = to_report_history_item(rec)
    assert "time_range=14d" in item["result_link"]


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
