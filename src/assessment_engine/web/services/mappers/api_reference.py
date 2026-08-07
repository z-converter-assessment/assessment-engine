"""OpenAPI 스펙 -> API 목록 ViewModel. 외부 연동 대상 JSON API 만 카탈로그로 노출한다.

노출 범위·제외 대상은 docs/reference/web/routers.md `GET /reference/api` 항목. 상세 파라미터·스키마·시험
실행은 Swagger(`/docs`)·ReDoc(`/redoc`) 단일 진실이라 본 페이지가 옮겨 적지 않는다.
"""

from assessment_engine.json_types import JsonObject, json_obj, json_str_list
from assessment_engine.web.view_models.api_reference import ApiEndpoint, ApiGroup, ApiParam

_HTTP_METHODS = ("get", "post", "put", "patch", "delete")

# method -> 뱃지 (배경, 글자) 색. 안전(GET)=파랑 · 변경(POST/PUT/PATCH)=녹색/노랑 · 삭제=빨강.
_METHOD_STYLE = {
    "GET": ("#dbeafe", "#1e40af"),
    "POST": ("#dcfce7", "#166534"),
    "PUT": ("#fef9c3", "#854d0e"),
    "PATCH": ("#fef9c3", "#854d0e"),
    "DELETE": ("#fee2e2", "#991b1b"),
}

# 노출 태그 화이트리스트 — 정의 순서가 곧 표시 순서, 미등재 태그는 자동 제외.
# 신규 태그를 도입하면 여기 실을지 검토한다.
_TAG_LABELS = [
    ("assessment", "통합 프로비저닝 어세스먼트"),
    ("right-sizing", "자원 적정성 판정"),
    ("exports", "어세스먼트 계약 다운로드"),
    ("tasks", "설치 작업"),
]
_ALLOWED_TAGS = frozenset(t for t, _ in _TAG_LABELS)


def _property_type(prop: JsonObject) -> str:
    if "type" in prop:
        return prop["type"]
    for opt in prop.get("anyOf", []):
        if opt.get("type") and opt["type"] != "null":
            return opt["type"]
    return "-"


def _resolve_body_fields(op: JsonObject, spec: JsonObject) -> list[ApiParam]:
    """요청 본문 스키마($ref)를 풀어 필드 목록 반환 — required 를 빼면 "전부 선택"으로 오인된다."""
    rb = json_obj(op, "requestBody")
    schema = json_obj(json_obj(json_obj(rb, "content"), "application/json"), "schema")
    ref = schema.get("$ref")
    if not isinstance(ref, str):
        return []
    name = ref.split("/")[-1]
    model = json_obj(json_obj(json_obj(spec, "components"), "schemas"), name)
    required = set(json_str_list(model, "required"))
    return [
        ApiParam(name=fname, location="body", required=fname in required, type=_property_type(prop))
        for fname, prop in (json_obj(model, "properties")).items()
    ]


def _display_summary(op: JsonObject) -> str:
    """목록 표시 요약 — FastAPI 가 함수명에서 만드는 영어 summary 대신 docstring 첫 줄을 쓴다."""
    desc = (op.get("description") or "").strip()
    if desc:
        return desc.splitlines()[0].strip()
    return op.get("summary", "")


def _returns_json(op: JsonObject) -> bool:
    """성공(2xx) 응답에 application/json 이 있는지 — 200 만 보면 201 뿐인 엔드포인트를 오탈락시킨다."""
    responses = json_obj(op, "responses")
    return any(
        "application/json" in json_obj(resp, "content") for code, resp in responses.items() if code.startswith("2")
    )


def build_api_reference(spec: JsonObject) -> list[ApiGroup]:
    """OpenAPI dict -> 태그별 ApiGroup list — `/api/*` 화이트리스트 태그의 JSON 엔드포인트만."""
    by_tag: dict[str, list[ApiEndpoint]] = {}
    for path, ops in sorted((json_obj(spec, "paths")).items()):
        if not path.startswith("/api/"):
            continue  # SSR 페이지 라우트 제외 — JSON API 만
        for method, op in ops.items():
            if method not in _HTTP_METHODS:
                continue
            tag = (op.get("tags") or ["기타"])[0]
            if tag not in _ALLOWED_TAGS:
                continue
            if not _returns_json(op):
                continue  # HTML fragment 응답(예: task 상세 modal)
            params = [
                ApiParam(
                    name=p.get("name", ""),
                    location=p.get("in", ""),
                    required=bool(p.get("required", False)),
                    type=(json_obj(p, "schema")).get("type", "-"),
                )
                for p in op.get("parameters", [])
            ]
            bg, fg = _METHOD_STYLE.get(method.upper(), ("#e2e8f0", "#475569"))
            by_tag.setdefault(tag, []).append(
                ApiEndpoint(
                    method=method.upper(),
                    path=path,
                    summary=_display_summary(op),
                    description=op.get("description", ""),
                    badge_bg=bg,
                    badge_fg=fg,
                    params=params,
                    body_fields=_resolve_body_fields(op, spec),
                )
            )
    order = {tag: i for i, (tag, _) in enumerate(_TAG_LABELS)}
    labels = dict(_TAG_LABELS)
    # by_tag 키는 _ALLOWED_TAGS 필터를 통과한 값만이라 order/labels 양쪽에 항상 존재 (fallback 불요).
    groups = [ApiGroup(tag=t, label=labels[t], endpoints=eps) for t, eps in by_tag.items()]
    groups.sort(key=lambda g: order[g.tag])
    return groups
