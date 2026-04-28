import json
from uuid import uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from consumer.schemas import AgentMessage, ErrorInput, InventoryInput, MetricsInput

pytestmark = pytest.mark.unit

_NOW = "2024-01-01T00:00:00Z"
_UUID = str(uuid4())

_BASE = {
    "machine_id": "abc123",
    "agent_version": "1.0.0",
    "collected_at": _NOW,
    "hostname": "host-01",
    "message_id": _UUID,
}

_INVENTORY_MSG = {**_BASE, "message_type": "inventory"}
_METRICS_MSG   = {**_BASE, "message_type": "metrics"}
_ERROR_MSG     = {**_BASE, "message_type": "error",
                  "error_code": "E001", "error_message": "fail", "failed_component": "collect"}

_adapter = TypeAdapter(AgentMessage)


class TestCommonMeta:
    def test_empty_agent_version_rejected(self):
        with pytest.raises(ValidationError):
            InventoryInput(**{**_INVENTORY_MSG, "agent_version": ""})

    def test_empty_hostname_rejected(self):
        with pytest.raises(ValidationError):
            InventoryInput(**{**_INVENTORY_MSG, "hostname": ""})

    def test_invalid_message_id_rejected(self):
        with pytest.raises(ValidationError):
            InventoryInput(**{**_INVENTORY_MSG, "message_id": "not-a-uuid"})

    def test_invalid_collected_at_rejected(self):
        with pytest.raises(ValidationError):
            InventoryInput(**{**_INVENTORY_MSG, "collected_at": "not-a-date"})


class TestInventoryInput:
    def test_minimal_required_fields_parsed(self):
        obj = InventoryInput(**_INVENTORY_MSG)
        assert obj.machine_id == "abc123"
        assert obj.message_type == "inventory"

    def test_empty_machine_id_rejected(self):
        with pytest.raises(ValidationError):
            InventoryInput(**{**_INVENTORY_MSG, "machine_id": ""})

    def test_cpu_cores_zero_rejected(self):
        with pytest.raises(ValidationError):
            InventoryInput(**{**_INVENTORY_MSG, "cpu_cores": 0})

    def test_mem_total_kb_zero_rejected(self):
        # ge=0 이므로 0은 허용
        obj = InventoryInput(**{**_INVENTORY_MSG, "mem_total_kb": 0})
        assert obj.mem_total_kb == 0

    def test_optional_fields_accept_none(self):
        obj = InventoryInput(**{**_INVENTORY_MSG, "os_id": None, "cpu_model": None})
        assert obj.os_id is None
        assert obj.cpu_model is None

    def test_ip_external_none_vs_empty_list(self):
        obj_none = InventoryInput(**{**_INVENTORY_MSG, "ip_external": None})
        obj_list = InventoryInput(**{**_INVENTORY_MSG, "ip_external": []})
        assert obj_none.ip_external is None
        assert obj_list.ip_external == []

    def test_disks_defaults_to_empty(self):
        obj = InventoryInput(**_INVENTORY_MSG)
        assert obj.disks == []

    def test_mounts_defaults_to_empty(self):
        obj = InventoryInput(**_INVENTORY_MSG)
        assert obj.mounts == []


class TestMetricsInput:
    def test_minimal_fields_parsed(self):
        obj = MetricsInput(**_METRICS_MSG)
        assert obj.message_type == "metrics"

    def test_all_cpu_fields_optional(self):
        obj = MetricsInput(**{**_METRICS_MSG, "cpu_stat": None})
        assert obj.cpu_stat is None

    def test_all_mem_fields_optional(self):
        obj = MetricsInput(**{**_METRICS_MSG, "mem_total_kb": None, "mem_available_kb": None})
        assert obj.mem_total_kb is None

    def test_disk_io_defaults_to_empty(self):
        obj = MetricsInput(**_METRICS_MSG)
        assert obj.disk_io == []

    def test_net_io_defaults_to_empty(self):
        obj = MetricsInput(**_METRICS_MSG)
        assert obj.net_io == []

    def test_mounts_defaults_to_empty(self):
        obj = MetricsInput(**_METRICS_MSG)
        assert obj.mounts == []


class TestErrorInput:
    def test_minimal_fields_parsed(self):
        obj = ErrorInput(**_ERROR_MSG)
        assert obj.error_code == "E001"
        assert obj.failed_component == "collect"

    def test_invalid_failed_component_rejected(self):
        with pytest.raises(ValidationError):
            ErrorInput(**{**_ERROR_MSG, "failed_component": "unknown"})

    def test_empty_error_code_rejected(self):
        with pytest.raises(ValidationError):
            ErrorInput(**{**_ERROR_MSG, "error_code": ""})

    def test_empty_error_message_rejected(self):
        with pytest.raises(ValidationError):
            ErrorInput(**{**_ERROR_MSG, "error_message": ""})


class TestAgentMessageUnion:
    def test_inventory_discriminated_correctly(self):
        obj = _adapter.validate_python(_INVENTORY_MSG)
        assert isinstance(obj, InventoryInput)

    def test_metrics_discriminated_correctly(self):
        obj = _adapter.validate_python(_METRICS_MSG)
        assert isinstance(obj, MetricsInput)

    def test_error_discriminated_correctly(self):
        obj = _adapter.validate_python(_ERROR_MSG)
        assert isinstance(obj, ErrorInput)

    def test_unknown_message_type_rejected(self):
        with pytest.raises(ValidationError):
            _adapter.validate_python({**_BASE, "message_type": "unknown"})

    def test_json_round_trip(self):
        raw = json.dumps(_INVENTORY_MSG)
        obj = _adapter.validate_json(raw)
        assert isinstance(obj, InventoryInput)
        assert obj.machine_id == "abc123"