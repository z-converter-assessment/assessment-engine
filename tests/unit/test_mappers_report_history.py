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


@pytest.mark.parametrize(
    ("job_type", "expected_view", "expected_label"),
    [
        ("customer_report", "customer", "고객 보고서"),
        ("engineer_report", "engineer", "엔지니어 보고서"),
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


_JOB_ID = "00000000-0000-0000-0000-000000000001"


def test_server_scope_single_server_link():
    rec = _rec(
        job_type="customer_report",
        scope="server",
        input_params={"server_public_ids": ["pid-only"], "time_range": "14d", "period_days": 14},
    )
    item = to_report_history_item(rec)
    assert item["result_link"] == f"/servers/pid-only/report?job={_JOB_ID}"
    assert item["server_count"] == 1


def test_server_scope_multi_server_link():
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
    rec = _rec(
        job_type="customer_report",
        scope="server",
        input_params={"time_range": "14d", "period_days": 14},
    )
    item = to_report_history_item(rec)
    assert item["server_count"] == 0
    assert item["result_link"] == f"/reports/servers?job={_JOB_ID}"


def test_environment_scope_link():
    rec = _rec(
        job_type="customer_report",
        scope="environment",
        input_params={"time_range": "14d", "period_days": 14},
        result={"snapshot": {"base": {"total": 55}}},
    )
    item = to_report_history_item(rec)
    assert item["result_link"] == f"/reports/environment?job={_JOB_ID}"
    assert item["server_count"] == 55


def test_environment_scope_count_none_without_snapshot():
    rec = _rec(
        job_type="customer_report",
        scope="environment",
        input_params={"time_range": "14d", "period_days": 14},
        result={},
    )
    item = to_report_history_item(rec)
    assert item["server_count"] is None


def test_link_with_back_appends_referrer():
    rec = _rec(
        job_type="engineer_report",
        scope="environment",
        input_params={"time_range": "30d"},
    )
    item = to_report_history_item(rec, back="%2Freports%2Fhistory")
    assert item["result_link"] == f"/reports/environment?job={_JOB_ID}&back=%2Freports%2Fhistory"


def test_window_label_uses_input_params_time_range():
    rec = _rec(
        job_type="customer_report",
        scope="server",
        input_params={"server_public_ids": ["pid"], "time_range": "1h", "period_days": 14},
    )
    item = to_report_history_item(rec)
    assert item["window_label"] == "1시간"


def test_window_label_fallback_to_period_days_integer():
    rec = _rec(
        job_type="customer_report",
        scope="server",
        input_params={"server_public_ids": ["pid"], "period_days": 7},
        result={},
    )
    item = to_report_history_item(rec)
    assert item["window_label"] == "7일"


def test_window_label_fallback_to_period_days_fraction():
    rec = _rec(
        job_type="customer_report",
        scope="server",
        input_params={"server_public_ids": ["pid"], "period_days": 0.5},
        result={},
    )
    item = to_report_history_item(rec)
    assert item["window_label"] == "0.5일"


def test_window_label_prefers_input_over_result():
    rec = _rec(
        job_type="customer_report",
        scope="server",
        input_params={"server_public_ids": ["pid"], "time_range": "6h"},
        result={"time_range": "30d"},
    )
    item = to_report_history_item(rec)
    assert item["window_label"] == "6시간"


def test_window_label_falls_back_to_result_time_range():
    rec = _rec(
        job_type="customer_report",
        scope="server",
        input_params={"server_public_ids": ["pid"]},
        result={"time_range": "24h"},
    )
    item = to_report_history_item(rec)
    assert item["window_label"] == "1일"


def test_window_label_default_when_both_missing():
    rec = _rec(
        job_type="customer_report",
        scope="server",
        input_params={"server_public_ids": ["pid"]},
        result=None,
    )
    item = to_report_history_item(rec)
    assert item["window_label"] == "14일"


def test_to_report_history_item_returns_all_required_keys():
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
