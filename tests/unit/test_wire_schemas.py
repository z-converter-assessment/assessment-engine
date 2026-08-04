"""wire 계약 게이트 — 예시 6종을 JSON Schema 와 인바운드 Pydantic 양쪽으로 검증.

정본 = docs/reference/contracts/wire.schema.json + wire-examples.json.
두 정본은 구조가 달라 직접 비교가 안 되므로 예시를 공통 증인으로 쓴다 — 스키마가 허용한다고
규정한 입력을 인바운드 스키마(consumer/schemas.py)가 실제로 수용하는지 같은 파일로 확인한다.
"""

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as JsonSchemaError
from pydantic import ValidationError

from assessment_engine.consumer.schemas import (
    BootInfo,
    ErrorInput,
    InventoryInput,
    MetricsInput,
    NonblockMountInfo,
    TaskResultInput,
)
from assessment_engine.json_types import JsonObject

_CONTRACTS = Path(__file__).resolve().parents[2] / "docs/reference/contracts"
_EXAMPLES = json.loads((_CONTRACTS / "wire-examples.json").read_text())
_SCHEMA = json.loads((_CONTRACTS / "wire.schema.json").read_text())
_MODEL_BY_TYPE = {
    "metrics": MetricsInput,
    "inventory": InventoryInput,
    "task.result": TaskResultInput,
    "error": ErrorInput,
}
def _schema_errors(msg: JsonObject) -> list[JsonSchemaError]:
    """정본 JSON Schema 위반 목록. jsonschema 가 iter_errors 를 타입 없이 선언해 여기서 한 번 확정한다."""
    validator = Draft202012Validator(_SCHEMA)
    return list(validator.iter_errors(msg))  # pyright: ignore[reportUnknownMemberType]


_CASES = [(name, msg) for name, msg in _EXAMPLES.items() if not name.startswith("_")]


def test_wire_schema_is_valid() -> None:
    """정본 스키마 자체가 Draft 2020-12 로 유효 — 오타 키워드는 조용히 무시되므로 먼저 막는다."""
    Draft202012Validator.check_schema(_SCHEMA)


@pytest.mark.parametrize("name,msg", _CASES, ids=[c[0] for c in _CASES])
def test_v2_example_matches_schema(name: str, msg: JsonObject) -> None:
    """계약 예시 6종이 정본 JSON Schema 를 만족."""
    errors = sorted(_schema_errors(msg), key=lambda e: e.json_path)
    assert not errors, "\n".join(f"{e.json_path}: {e.message}" for e in errors)


@pytest.mark.parametrize("name,msg", _CASES, ids=[c[0] for c in _CASES])
def test_v2_example_validates(name: str, msg: JsonObject) -> None:
    """계약 예시 6종(linux/windows metrics·inventory + task.result + error)이 인바운드 스키마로 파싱."""
    model = _MODEL_BY_TYPE[msg["message_type"]]
    model.model_validate(msg)


def test_metrics_datapoint_array_shape() -> None:
    """metrics = system.* 네임스페이스(datapoint-array). 필수 4축 present, Windows pressure=null."""
    lin = MetricsInput.model_validate(_EXAMPLES["linux_metrics"])
    assert lin.system_cpu is not None
    assert "cpu.time" in lin.system_cpu
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


@pytest.mark.parametrize(
    "version,accepted",
    [
        ("1.0", True),
        ("1.1", True),  # minor additive — silent 호환
        ("1.12", True),
        ("2.0", False),  # major 전환 — flag-day
        ("0.9", False),
        ("1", False),  # major.minor 형식 위반
    ],
)
def test_schema_version_major_gate(version: str, accepted: bool) -> None:
    """major 일치만 통과. 두 정본이 같은 입력에 같은 판정을 내리는지 함께 확인한다."""
    msg = _EXAMPLES["error"] | {"schema_version": version}
    schema_ok = not _schema_errors(msg)
    try:
        ErrorInput.model_validate(msg)
        pydantic_ok = True
    except ValidationError:
        pydantic_ok = False
    assert (schema_ok, pydantic_ok) == (accepted, accepted)


def test_inventory_reproduction_descriptors_parse() -> None:
    """reproduction 재현 서술자 — flat 필드 + BootInfo/NonblockMountInfo nested 파싱."""
    payload = dict(_EXAMPLES["linux_inventory"])
    payload.update(
        arch="x86_64",
        bits=64,
        boot_firmware="uefi",
        secure_boot=True,
        edition=None,
        timezone="Asia/Seoul",
        rtc_utc=True,
        boot={"kernel_cmdline": "ro quiet", "root_ref_type": "label", "grub_install_target": None},
        nonblock_mounts=[
            {"source": "tmpfs", "target": "/run", "fstype": "tmpfs",
             "options": ["rw", "nosuid"], "fs_freq": 0, "fs_passno": 0}
        ],
    )
    inv = InventoryInput.model_validate(payload)
    assert inv.arch == "x86_64"
    assert inv.bits == 64
    assert inv.boot_firmware == "uefi"
    assert inv.secure_boot is True
    assert inv.edition is None
    assert inv.timezone == "Asia/Seoul"
    assert inv.rtc_utc is True
    assert isinstance(inv.boot, BootInfo)
    assert inv.boot.root_ref_type == "label"
    assert inv.boot.grub_install_target is None
    assert inv.nonblock_mounts is not None
    assert len(inv.nonblock_mounts) == 1
    assert isinstance(inv.nonblock_mounts[0], NonblockMountInfo)
    assert inv.nonblock_mounts[0].fstype == "tmpfs"
    assert inv.nonblock_mounts[0].options == ["rw", "nosuid"]


def test_inventory_reproduction_descriptors_default_none() -> None:
    """boot/nonblock_mounts 키 부재 시 None 기본값 (Windows/구 agent forward-compat)."""
    inv = InventoryInput.model_validate(_EXAMPLES["linux_inventory"])
    assert inv.boot is None
    assert inv.nonblock_mounts is None
    assert inv.arch is None and inv.bits is None
