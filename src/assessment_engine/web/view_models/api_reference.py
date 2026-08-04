"""API 목록 페이지 ViewModel — OpenAPI 스펙(app.openapi())에서 도출한 JSON API 카탈로그.

조립은 mappers/api_reference.build_api_reference 단일 책임 (본 dataclass 는 결과 형태만).
스펙이 코드(라우터)에서 자동 생성되므로 drift 0 (F12) — 손으로 유지하는 표 아님.
"""

from dataclasses import dataclass, field


@dataclass
class ApiParam:
    name: str  # 파라미터 이름
    location: str  # query / path
    required: bool
    type: str  # schema type (string/integer/...)


@dataclass
class ApiEndpoint:
    method: str  # GET / POST / ...
    path: str  # "/api/servers/{server_id}/metrics/chart"
    summary: str  # 라우트 요약 (docstring 첫 줄 / summary)
    description: str  # 상세 설명
    badge_bg: str = "#e2e8f0"  # method 뱃지 배경 — mapper precompute (P3)
    badge_fg: str = "#475569"  # method 뱃지 글자색
    params: list[ApiParam] = field(default_factory=list[ApiParam])
    # 요청 본문(POST 등) 필드 — $ref 스키마 property + required(스키마 `required` 배열 기준). ApiParam 재사용
    # (location="body") — 필수 표시(*)를 query/path 파라미터와 동일 렌더링으로 통일.
    body_fields: list[ApiParam] = field(default_factory=list[ApiParam])


@dataclass
class ApiGroup:
    tag: str  # OpenAPI tag ("api" / "exports")
    label: str  # 한국어 표시 라벨
    endpoints: list[ApiEndpoint] = field(default_factory=list[ApiEndpoint])
