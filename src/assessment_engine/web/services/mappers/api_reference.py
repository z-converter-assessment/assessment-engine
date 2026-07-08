"""OpenAPI 스펙 -> API 목록 ViewModel (P2). 라우터에서 자동 생성된 스펙을 표시 구조로 변환.

JSON API(`/api/*`)만 노출 — SSR 페이지 라우트는 제외. 태그(api/exports)로 그룹핑.
스펙이 코드 단일 진실이라 drift 0 (F12) — 손으로 유지 안 함.
"""

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

# 태그 -> 한국어 라벨 + 표시 순서. 미정의 태그는 태그명 그대로 뒤에 붙임.
_TAG_LABELS = [
    ("right-sizing", "자원 적정성 판정 (프로비저닝)"),
    ("exports", "JSON Export (외부 연동)"),
    ("api", "서버·메트릭 조회 (화면 데이터)"),
    ("tasks", "설치 작업 (Install Task)"),
]


def _resolve_body_fields(op: dict, spec: dict) -> list[str]:
    """POST 등 요청 본문 스키마($ref)를 풀어 property 이름 목록 반환 (본문 필드 표시용)."""
    rb = op.get("requestBody") or {}
    schema = (((rb.get("content") or {}).get("application/json") or {}).get("schema")) or {}
    ref = schema.get("$ref")
    if not ref:
        return []
    name = ref.split("/")[-1]
    model = ((spec.get("components") or {}).get("schemas") or {}).get(name) or {}
    return list((model.get("properties") or {}).keys())


def _display_summary(op: dict) -> str:
    """목록 표시 요약 — docstring 첫 줄(한국어) 우선. OpenAPI summary 는 FastAPI 가 함수명에서 자동 생성한
    영어("Get Right Sizing")라 Korean UI 에 부적합 -> description(=docstring) 첫 줄을 요약으로 쓴다.
    """
    desc = (op.get("description") or "").strip()
    if desc:
        return desc.splitlines()[0].strip()
    return op.get("summary", "")


def build_api_reference(spec: dict) -> list[ApiGroup]:
    """OpenAPI dict -> ApiGroup list (태그별). `/api/*` JSON 엔드포인트만, 태그 정의 순서로 정렬."""
    by_tag: dict[str, list[ApiEndpoint]] = {}
    for path, ops in sorted((spec.get("paths") or {}).items()):
        if not path.startswith("/api/"):
            continue  # SSR 페이지 라우트 제외 — JSON API 만
        if path == "/api/right-sizing":
            continue  # 전용 상세 섹션(api_reference.html)이 대신 문서화 — 자동 목록 중복 제거
        for method, op in ops.items():
            if method not in _HTTP_METHODS:
                continue
            tag = (op.get("tags") or ["기타"])[0]
            params = [
                ApiParam(
                    name=p.get("name", ""),
                    location=p.get("in", ""),
                    required=bool(p.get("required", False)),
                    type=(p.get("schema") or {}).get("type", "-"),
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
    groups = [ApiGroup(tag=t, label=labels.get(t, t), endpoints=eps) for t, eps in by_tag.items()]
    groups.sort(key=lambda g: order.get(g.tag, len(order)))
    return groups
