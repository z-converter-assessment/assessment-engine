"""wire v2 인바운드 Pydantic 스키마 — 계약 예시 6종 라운드트립 검증.

정본 = docs/reference/contracts/wire.schema.v2.json + v2-example-messages.json.
엔진 인바운드 스키마(consumer/schemas.py)가 계약 예시를 전부 수용하는지 = ingest 진입 계약 정합.
"""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from assessment_engine.consumer.schemas import (
    ErrorInput,
    InventoryInput,
    MetricsInput,
    TaskResultInput,
)

_EXAMPLES = json.loads(
    (Path(__file__).resolve().parents[2] / "docs/reference/contracts/v2-example-messages.json").read_text()
)
_MODEL_BY_TYPE = {
    "metrics": MetricsInput,
    "inventory": InventoryInput,
    "task.result": TaskResultInput,
    "error": ErrorInput,
}
_CASES = [(name, msg) for name, msg in _EXAMPLES.items() if not name.startswith("_")]


@pytest.mark.parametrize("name,msg", _CASES, ids=[c[0] for c in _CASES])
def test_v2_example_validates(name: str, msg: dict) -> None:
    """계약 예시 6종(linux/windows metrics·inventory + task.result + error)이 인바운드 스키마로 파싱."""
    model = _MODEL_BY_TYPE[msg["message_type"]]
    model.model_validate(msg)


def test_metrics_datapoint_array_shape() -> None:
    """metrics = system.* 네임스페이스(datapoint-array). 필수 4축 present, Windows pressure=null."""
    lin = MetricsInput.model_validate(_EXAMPLES["linux_metrics"])
    assert "cpu.time" in (lin.system_cpu or {})
    assert lin.system_cpu["cpu.time"].type == "counter"
    assert lin.system_cpu["cpu.time"].unit == "s"
    # per-cpu x state 는 points 배열, attr 로 구분
    pts = lin.system_cpu["cpu.time"].points
    assert any(p.attr.get("state") == "idle" for p in pts)
    win = MetricsInput.model_validate(_EXAMPLES["windows_metrics"])
    assert win.system_pressure is None  # Windows PSI 미지원


def test_inventory_v2_arrays() -> None:
    """inventory = 정적 서술자 + block_devices/net_interfaces/lvm_vgs. mem_total_bytes(By)."""
    lin = InventoryInput.model_validate(_EXAMPLES["linux_inventory"])
    assert lin.hostname and lin.os_id and lin.mem_total_bytes
    assert lin.block_devices and lin.block_devices[0].id_type
    assert lin.net_interfaces[0].id_type == "mac"
    assert lin.net_interfaces[0].addresses[0].family in ("ipv4", "ipv6")
    assert lin.lvm_vgs  # Linux VG
    win = InventoryInput.model_validate(_EXAMPLES["windows_inventory"])
    assert not win.lvm_vgs  # Windows 미발행


def test_task_result_v2_policy() -> None:
    """task.result = task_policy(exit_code 우선) + free-string task_id/status."""
    tr = TaskResultInput.model_validate(_EXAMPLES["task_result"])
    assert tr.task_id  # free string
    assert tr.agent_started_at is None  # worker 컨텍스트 항상 null
    assert hasattr(tr, "task_policy")


def test_schema_version_required() -> None:
    """schema_version 없으면 거부 (v1 flag-day cutover)."""
    bad = {k: v for k, v in _EXAMPLES["error"].items() if k != "schema_version"}
    with pytest.raises(ValidationError):
        ErrorInput.model_validate(bad)
