"""
스키마 유효성 검증 테스트.
검증 규칙(3-Tier)을 테스트한다 — 필드 값 자체보다 수용/거부 여부에 집중.

필드 추가·삭제 시 _INVENTORY / _METRICS / _ERROR 상수와 해당 클래스만 수정.
"""
import json

import pytest
from pydantic import TypeAdapter, ValidationError

from consumer.schemas import AgentMessage, ErrorInput, InventoryInput, MetricsInput

# ---------------------------------------------------------------------------
# 최소 유효 페이로드 (필수 필드만)
# ---------------------------------------------------------------------------

_BASE = {
    "agent_version": "1.0.0",
    "collected_at": "2026-01-01T00:00:00Z",
    "hostname": "host01",
    "message_id": "00000000-0000-0000-0000-000000000001",
}

_INVENTORY = {
    **_BASE,
    "message_type": "inventory",
    "machine_id": "abc123",
    "kernel_version": "5.15.0",
    "cpu_cores": 4,
    "mem_total_mb": 8192,
}

_METRICS = {
    **_BASE,
    "message_type": "metrics",
    "cpu_user_pct": 20.0,
    "cpu_system_pct": 5.0,
    "cpu_iowait_pct": 1.0,
    "mem_used_mb": 4096,
    "swap_used_mb": 0,
    "load_1m": 1.0,
}

_ERROR = {
    **_BASE,
    "message_type": "error",
    "error_code": "TEST_ERR",
    "error_message": "something failed",
    "failed_component": "collect",
    "timestamp": "2026-01-01T00:00:00Z",
}


# ---------------------------------------------------------------------------
# 공통 메타 (모든 메시지 타입 공유)
# ---------------------------------------------------------------------------

class TestCommonMeta:
    @pytest.mark.parametrize("field", ["agent_version", "hostname"])
    def test_empty_string_required_field_rejected(self, field):
        with pytest.raises(ValidationError):
            InventoryInput(**{**_INVENTORY, field: ""})

    def test_invalid_uuid_rejected(self):
        with pytest.raises(ValidationError):
            InventoryInput(**{**_INVENTORY, "message_id": "not-a-uuid"})

    def test_invalid_datetime_rejected(self):
        with pytest.raises(ValidationError):
            InventoryInput(**{**_INVENTORY, "collected_at": "not-a-date"})


# ---------------------------------------------------------------------------
# InventoryInput
# ---------------------------------------------------------------------------

class TestInventoryInput:
    def test_minimal_required_fields_parse(self):
        m = InventoryInput(**_INVENTORY)
        assert m.message_type == "inventory"

    # Required — 누락·빈 값 거부
    @pytest.mark.parametrize("field", ["machine_id", "kernel_version"])
    def test_required_str_field_empty_rejected(self, field):
        with pytest.raises(ValidationError):
            InventoryInput(**{**_INVENTORY, field: ""})

    def test_cpu_cores_zero_rejected(self):
        with pytest.raises(ValidationError):
            InventoryInput(**{**_INVENTORY, "cpu_cores": 0})

    def test_mem_total_mb_zero_rejected(self):
        with pytest.raises(ValidationError):
            InventoryInput(**{**_INVENTORY, "mem_total_mb": 0})

    # Optional — None 허용
    @pytest.mark.parametrize("field", ["os_id", "os_version", "cpu_model"])
    def test_optional_field_accepts_none(self, field):
        m = InventoryInput(**{**_INVENTORY, field: None})
        assert getattr(m, field) is None

    def test_ip_external_none_means_collect_failed(self):
        m = InventoryInput(**{**_INVENTORY, "ip_external": None})
        assert m.ip_external is None

    def test_ip_external_empty_list_means_no_external_ip(self):
        m = InventoryInput(**{**_INVENTORY, "ip_external": []})
        assert m.ip_external == []

    # Empty-OK — 빈 배열 허용
    @pytest.mark.parametrize("field", ["ip_internal", "disks", "disk_usage"])
    def test_empty_ok_field_accepts_empty_list(self, field):
        m = InventoryInput(**{**_INVENTORY, field: []})
        assert getattr(m, field) == []

    @pytest.mark.parametrize("field", ["ip_internal", "disks", "disk_usage"])
    def test_empty_ok_field_defaults_to_empty(self, field):
        payload = {k: v for k, v in _INVENTORY.items() if k != field}
        m = InventoryInput(**payload)
        assert getattr(m, field) == []


# ---------------------------------------------------------------------------
# MetricsInput
# ---------------------------------------------------------------------------

class TestMetricsInput:
    def test_minimal_required_fields_parse(self):
        m = MetricsInput(**_METRICS)
        assert m.message_type == "metrics"

    # Required — 범위 위반 거부
    @pytest.mark.parametrize("field", ["cpu_user_pct", "cpu_system_pct", "cpu_iowait_pct"])
    def test_cpu_pct_over_100_rejected(self, field):
        with pytest.raises(ValidationError):
            MetricsInput(**{**_METRICS, field: 100.1})

    @pytest.mark.parametrize("field", ["mem_used_mb", "swap_used_mb"])
    def test_negative_mem_rejected(self, field):
        with pytest.raises(ValidationError):
            MetricsInput(**{**_METRICS, field: -1})

    def test_negative_load_rejected(self):
        with pytest.raises(ValidationError):
            MetricsInput(**{**_METRICS, "load_1m": -0.1})

    # Optional — None 허용 (기동 직후 delta 없음, 구형 커널)
    @pytest.mark.parametrize("field", [
        "mem_available_mb",
        "disk_read_iops", "disk_write_iops",
        "disk_read_bytes", "disk_write_bytes",
        "net_rx_bytes", "net_tx_bytes",
    ])
    def test_optional_field_defaults_to_none(self, field):
        payload = {k: v for k, v in _METRICS.items() if k != field}
        m = MetricsInput(**payload)
        assert getattr(m, field) is None

    # Empty-OK
    def test_disk_usage_accepts_empty_list(self):
        m = MetricsInput(**{**_METRICS, "disk_usage": []})
        assert m.disk_usage == []

    def test_disk_usage_defaults_to_empty(self):
        payload = {k: v for k, v in _METRICS.items() if k != "disk_usage"}
        m = MetricsInput(**payload)
        assert m.disk_usage == []


# ---------------------------------------------------------------------------
# ErrorInput
# ---------------------------------------------------------------------------

class TestErrorInput:
    def test_minimal_required_fields_parse(self):
        m = ErrorInput(**_ERROR)
        assert m.message_type == "error"

    def test_invalid_component_rejected(self):
        with pytest.raises(ValidationError):
            ErrorInput(**{**_ERROR, "failed_component": "unknown"})

    @pytest.mark.parametrize("field", ["error_code", "error_message"])
    def test_empty_required_str_rejected(self, field):
        with pytest.raises(ValidationError):
            ErrorInput(**{**_ERROR, field: ""})


# ---------------------------------------------------------------------------
# discriminated union
# ---------------------------------------------------------------------------

class TestAgentMessageUnion:
    _adapter = TypeAdapter(AgentMessage)

    def test_inventory_routed_to_correct_type(self):
        result = self._adapter.validate_python(_INVENTORY)
        assert isinstance(result, InventoryInput)

    def test_metrics_routed_to_correct_type(self):
        result = self._adapter.validate_python(_METRICS)
        assert isinstance(result, MetricsInput)

    def test_error_routed_to_correct_type(self):
        result = self._adapter.validate_python(_ERROR)
        assert isinstance(result, ErrorInput)

    def test_unknown_message_type_rejected(self):
        with pytest.raises(ValidationError):
            self._adapter.validate_python({**_BASE, "message_type": "unknown"})

    def test_json_round_trip(self):
        result = self._adapter.validate_json(json.dumps(_METRICS))
        assert isinstance(result, MetricsInput)