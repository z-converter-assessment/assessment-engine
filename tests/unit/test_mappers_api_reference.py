"""api_reference mapper 단위 테스트 — 외부 연동 화이트리스트 태그 필터·JSON-only 필터 (P2)."""

from assessment_engine.web.services.mappers.api_reference import build_api_reference

_SPEC: dict = {
    "paths": {
        "/servers": {  # /api/ 접두 아님 — SSR 페이지 라우트, 항상 제외
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
        "/api/tasks/{task_id}/detail": {  # HTML fragment — task-modal 전용, JSON API 아님
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
        "/api/servers/{server_id}/metrics/chart": {  # 화면 전용 내부 데이터 — 화이트리스트 밖
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
    """'api' 태그(화면 데이터 조회)는 외부 연동 화이트리스트 밖 — 자동 제외."""
    groups = build_api_reference(_SPEC)
    all_paths = [e.path for g in groups for e in g.endpoints]
    assert "/api/servers/{server_id}/metrics/chart" not in all_paths
    assert all(g.tag != "api" for g in groups)


def test_excludes_html_fragment_endpoint():
    """200 응답이 application/json 이 아니면(HTML fragment) 제외."""
    groups = build_api_reference(_SPEC)
    all_paths = [e.path for g in groups for e in g.endpoints]
    assert "/api/tasks/{task_id}/detail" not in all_paths
    tasks_group = next(g for g in groups if g.tag == "tasks")
    assert [e.path for e in tasks_group.endpoints] == ["/api/tasks", "/api/tasks/install"]


def test_body_field_required_marked_from_schema_required_array():
    """요청 본문 필드도 query/path 파라미터와 동일하게 required 표시 — 스키마 `required` 배열 기준.

    표시 누락 시 필수 필드(target_public_ids) 없이도 호출 가능하다고 오인하기 쉽다(실사용 혼선으로 발견).
    """
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
    """200 하나만 보면 201 Created 전용 엔드포인트를 오탈락시킨다 — 모든 2xx 응답 검사."""
    spec = {
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
