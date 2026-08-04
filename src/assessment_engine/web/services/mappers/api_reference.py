"""OpenAPI 스펙 -> API 목록 ViewModel (P2). 라우터에서 자동 생성된 스펙을 표시 구조로 변환.

외부에서 실제로 호출할 가치가 있는 JSON API만 노출 — SSR 페이지 라우트(#F 범위 밖)·화면 전용 내부 데이터
엔드포인트(`api` 태그 — 차트 fetch·fleet-status polling 등 SSR 페이지 자체 JS 전용, 외부 연동 대상 아님)는
제외. 상세 파라미터·스키마·시험 실행은 Swagger(`/docs`)/ReDoc(`/redoc`) 단일 진실 — 본 페이지는 카탈로그만.
스펙이 코드 단일 진실이라 drift 0 (F12) — 손으로 유지 안 함.
"""

from assessment_engine.json_types import JsonObject, json_obj, json_str_list
from assessment_engine.web.view_models.api_reference import ApiEndpoint, ApiGroup, ApiParam

_HTTP_METHODS = ("get", "post", "put", "patch", "delete")

# method -> 뱃지 (배경, 글자) 색. 안전(GET)=파랑, 변경(POST/PUT)=녹색/노랑, 삭제=빨강.
_METHOD_STYLE = {
    "GET": ("#dbeafe", "#1e40af"),
    "POST": ("#dcfce7", "#166534"),
    "PUT": ("#fef9c3", "#854d0e"),
    "PATCH": ("#fef9c3", "#854d0e"),
    "DELETE": ("#fee2e2", "#991b1b"),
}

# 노출 태그 화이트리스트(+ 한국어 라벨, 표시 순서) — 외부 연동 관점 유의미한 4개만. 미등재 태그(예: 화면
# 전용 데이터 조회 "api")는 자동 제외(#F9 신규 태그 도입 시 본 목록 검토 의무).
_TAG_LABELS = [
    ("assessment", "통합 프로비저닝 어세스먼트"),
    ("right-sizing", "자원 적정성 판정"),
    ("exports", "어세스먼트 계약 다운로드"),
    ("tasks", "설치 작업"),
]
_ALLOWED_TAGS = frozenset(t for t, _ in _TAG_LABELS)


def _property_type(prop: JsonObject) -> str:
    """스키마 property 표시 타입 — 단순 `type` 우선, optional(`anyOf: [T, null]`) 은 null 아닌 쪽 타입."""
    if "type" in prop:
        return prop["type"]
    for opt in prop.get("anyOf", []):
        if opt.get("type") and opt["type"] != "null":
            return opt["type"]
    return "-"


def _resolve_body_fields(op: JsonObject, spec: JsonObject) -> list[ApiParam]:
    """POST 등 요청 본문 스키마($ref)를 풀어 필드 목록 반환 — required(스키마 `required` 배열)까지 표시해야

    한다: required 미표시로 "전부 선택"처럼 보이면 실제로는 필수인 필드(예: InstallRequest.target_public_ids)
    없이 호출 가능하다고 오인하기 쉽다(query/path 파라미터의 `*` 표시와 동일 원칙, 화면 간 표현 통일).
    """
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
    """목록 표시 요약 — docstring 첫 줄(한국어) 우선. OpenAPI summary 는 FastAPI 가 함수명에서 자동 생성한

    영어("Get Right Sizing")라 Korean UI 에 부적합 -> description(=docstring) 첫 줄을 요약으로 쓴다.
    """
    desc = (op.get("description") or "").strip()
    if desc:
        return desc.splitlines()[0].strip()
    return op.get("summary", "")


def _returns_json(op: JsonObject) -> bool:
    """성공(2xx) 응답 중 하나라도 application/json 을 포함하는지 — HTML fragment 엔드포인트(모달 등, 내부

    UI 전용) 제외 판별. 200 하나만 보면 201 Created 등만 쓰는 엔드포인트를 오탈락시킨다.
    """
    responses = json_obj(op, "responses")
    return any(
        "application/json" in json_obj(resp, "content") for code, resp in responses.items() if code.startswith("2")
    )


def build_api_reference(spec: JsonObject) -> list[ApiGroup]:
    """OpenAPI dict -> ApiGroup list (태그별). `/api/*` 중 화이트리스트 태그(_ALLOWED_TAGS)의 JSON 엔드포인트만,

    태그 정의 순서로 정렬.
    """
    by_tag: dict[str, list[ApiEndpoint]] = {}
    for path, ops in sorted((json_obj(spec, "paths")).items()):
        if not path.startswith("/api/"):
            continue  # SSR 페이지 라우트 제외 — JSON API 만
        for method, op in ops.items():
            if method not in _HTTP_METHODS:
                continue
            tag = (op.get("tags") or ["기타"])[0]
            if tag not in _ALLOWED_TAGS:
                continue  # 화면 전용 내부 데이터 조회 등 — 외부 연동 카탈로그 제외
            if not _returns_json(op):
                continue  # HTML fragment 응답(예: task 상세 modal) — JSON API 아님
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
