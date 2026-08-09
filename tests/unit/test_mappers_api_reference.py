from typing import TYPE_CHECKING

from assessment_engine.web.services.mappers.api_reference import build_api_reference

if TYPE_CHECKING:
    from assessment_engine.json_types import JsonObject

_SPEC: JsonObject = {
    "paths": {
        "/servers": {
            "get": {"tags": ["pages"], "responses": {"200": {"content": {"text/html": {}}}}},
        },
        "/api/assessment": {
            "get": {
                "tags": ["assessment"],
                "description": "통합 프로비저닝 어세스먼트.\n상세 설명.",
                "responses": {"200": {"content": {"application/json": {}}}},
                "parameters": [],
            },
        },
        "/api/right-sizing": {
            "get": {
                "tags": ["right-sizing"],
                "description": "자원 적정성 판정.",
                "responses": {"200": {"content": {"application/json": {}}}},
                "parameters": [],
            },
        },
        "/api/exports/inventory": {
            "post": {
                "tags": ["exports"],
                "description": "인벤토리 export.",
                "responses": {"200": {"content": {"application/json": {}}}},
                "parameters": [],
            },
        },
        "/api/tasks": {
            "get": {
                "tags": ["tasks"],
                "description": "최근 task 목록.",
                "responses": {"200": {"content": {"application/json": {}}}},
                "parameters": [],
            },
        },
        "/api/tasks/{task_id}/detail": {
            "get": {
                "tags": ["tasks"],
                "description": "task 상세 HTML fragment.",
                "responses": {"200": {"content": {"text/html": {}}}},
                "parameters": [],
            },
        },
        "/api/tasks/install": {
            "post": {
                "tags": ["tasks"],
                "description": "Install 발행.",
                "responses": {"200": {"content": {"application/json": {}}}},
                "parameters": [],
                "requestBody": {
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/InstallRequest"}}}
                },
            },
        },
        "/api/servers/{server_id}/metrics/chart": {
            "get": {
                "tags": ["api"],
                "description": "차트 데이터.",
                "responses": {"200": {"content": {"application/json": {}}}},
                "parameters": [],
            },
        },
    },
    "components": {
        "schemas": {
            "InstallRequest": {
                "type": "object",
                "required": ["target_public_ids"],
                "properties": {
                    "target_public_ids": {"type": "array"},
                    "zdm_ip": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                    "zdm_user": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                },
            },
        },
    },
}


def test_excludes_non_api_prefix_paths():
    groups = build_api_reference(_SPEC)
    all_paths = [e.path for g in groups for e in g.endpoints]
    assert "/servers" not in all_paths


def test_excludes_internal_screen_only_tag():
    groups = build_api_reference(_SPEC)
    all_paths = [e.path for g in groups for e in g.endpoints]
    assert "/api/servers/{server_id}/metrics/chart" not in all_paths
    assert all(g.tag != "api" for g in groups)


def test_excludes_html_fragment_endpoint():
    groups = build_api_reference(_SPEC)
    all_paths = [e.path for g in groups for e in g.endpoints]
    assert "/api/tasks/{task_id}/detail" not in all_paths
    tasks_group = next(g for g in groups if g.tag == "tasks")
    assert [e.path for e in tasks_group.endpoints] == ["/api/tasks", "/api/tasks/install"]


def test_body_field_required_marked_from_schema_required_array():
    groups = build_api_reference(_SPEC)
    tasks_group = next(g for g in groups if g.tag == "tasks")
    install_ep = next(e for e in tasks_group.endpoints if e.path == "/api/tasks/install")
    by_name = {f.name: f for f in install_ep.body_fields}
    assert by_name["target_public_ids"].required is True
    assert by_name["zdm_ip"].required is False
    assert by_name["zdm_user"].required is False
    assert by_name["target_public_ids"].location == "body"


def test_includes_whitelisted_tags_in_defined_order():
    groups = build_api_reference(_SPEC)
    assert [g.tag for g in groups] == ["assessment", "right-sizing", "exports", "tasks"]


def test_assessment_endpoint_labeled_and_summarized():
    groups = build_api_reference(_SPEC)
    assessment_group = next(g for g in groups if g.tag == "assessment")
    assert assessment_group.label == "통합 프로비저닝 어세스먼트"
    assert assessment_group.endpoints[0].summary == "통합 프로비저닝 어세스먼트."


def test_includes_endpoint_whose_only_success_code_is_201():
    spec: JsonObject = {
        "paths": {
            "/api/right-sizing/refresh": {
                "post": {
                    "tags": ["right-sizing"],
                    "description": "재계산 트리거.",
                    "responses": {"201": {"content": {"application/json": {}}}},
                    "parameters": [],
                },
            },
        },
    }
    groups = build_api_reference(spec)
    all_paths = [e.path for g in groups for e in g.endpoints]
    assert "/api/right-sizing/refresh" in all_paths
