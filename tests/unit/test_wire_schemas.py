import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from assessment_engine.consumer.schemas import (
    BootInfo,
    ErrorInput,
    InventoryInput,
    MetricsInput,
    NonblockMountInfo,
    TaskResultInput,
)

if TYPE_CHECKING:
    from jsonschema.exceptions import ValidationError as JsonSchemaError

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
    validator = Draft202012Validator(_SCHEMA)
    return list(validator.iter_errors(msg))  # pyright: ignore[reportUnknownMemberType]


_CASES = [(name, msg) for name, msg in _EXAMPLES.items() if not name.startswith("_")]


def test_wire_schema_is_valid() -> None:
    Draft202012Validator.check_schema(_SCHEMA)


@pytest.mark.parametrize(("name", "msg"), _CASES, ids=[c[0] for c in _CASES])
def test_v2_example_matches_schema(name: str, msg: JsonObject) -> None:
    errors = sorted(_schema_errors(msg), key=lambda e: e.json_path)
    assert not errors, "\n".join(f"{e.json_path}: {e.message}" for e in errors)


@pytest.mark.parametrize(("name", "msg"), _CASES, ids=[c[0] for c in _CASES])
def test_v2_example_validates(name: str, msg: JsonObject) -> None:
    model = _MODEL_BY_TYPE[msg["message_type"]]
    model.model_validate(msg)


def test_metrics_datapoint_array_shape() -> None:
    lin = MetricsInput.model_validate(_EXAMPLES["linux_metrics"])
    assert lin.system_cpu is not None
    assert "cpu.time" in lin.system_cpu
    assert lin.system_cpu["cpu.time"].type == "counter"
    assert lin.system_cpu["cpu.time"].unit == "s"
    pts = lin.system_cpu["cpu.time"].points
    assert any(p.attr.get("state") == "idle" for p in pts)
    win = MetricsInput.model_validate(_EXAMPLES["windows_metrics"])
    assert win.system_pressure is None


def test_inventory_v2_arrays() -> None:
    lin = InventoryInput.model_validate(_EXAMPLES["linux_inventory"])
    assert lin.hostname
    assert lin.os_id
    assert lin.mem_total_bytes
    assert lin.block_devices
    assert lin.block_devices[0].id_type
    assert lin.net_interfaces[0].id_type == "mac"
    assert lin.net_interfaces[0].addresses[0].family in ("ipv4", "ipv6")
    assert lin.lvm_vgs
    win = InventoryInput.model_validate(_EXAMPLES["windows_inventory"])
    assert not win.lvm_vgs


def test_task_result_v2_policy() -> None:
    tr = TaskResultInput.model_validate(_EXAMPLES["task_result"])
    assert tr.task_id
    assert tr.agent_started_at is None
    assert hasattr(tr, "task_policy")


def test_schema_version_required() -> None:
    bad = {k: v for k, v in _EXAMPLES["error"].items() if k != "schema_version"}
    with pytest.raises(ValidationError):
        ErrorInput.model_validate(bad)


@pytest.mark.parametrize(
    ("version", "accepted"),
    [
        ("1.0", True),
        ("1.1", True),
        ("1.12", True),
        ("2.0", False),
        ("0.9", False),
        ("1", False),
    ],
)
def test_schema_version_major_gate(version: str, accepted: bool) -> None:
    msg = _EXAMPLES["error"] | {"schema_version": version}
    schema_ok = not _schema_errors(msg)
    try:
        ErrorInput.model_validate(msg)
        pydantic_ok = True
    except ValidationError:
        pydantic_ok = False
    assert (schema_ok, pydantic_ok) == (accepted, accepted)


def test_inventory_reproduction_descriptors_parse() -> None:
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
            {
                "source": "tmpfs",
                "target": "/run",
                "fstype": "tmpfs",
                "options": ["rw", "nosuid"],
                "fs_freq": 0,
                "fs_passno": 0,
            }
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
    inv = InventoryInput.model_validate(_EXAMPLES["linux_inventory"])
    assert inv.boot is None
    assert inv.nonblock_mounts is None
    assert inv.arch is None
    assert inv.bits is None
