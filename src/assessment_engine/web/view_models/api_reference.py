"""API 목록 페이지 ViewModel — OpenAPI 스펙(app.openapi())에서 도출한 JSON API 카탈로그.

조립은 mappers/api_reference.build_api_reference 단일 책임 (본 dataclass 는 결과 형태만).
스펙이 코드(라우터)에서 자동 생성되므로 drift 0 (F12) — 손으로 유지하는 표 아님.
"""

from dataclasses import dataclass, field


@dataclass
class ApiParam:
    name: str
    location: str
    required: bool
    type: str  # schema type (string/integer/...)


@dataclass
class ApiEndpoint:
    method: str
    path: str
    summary: str
    description: str
    badge_bg: str = "#e2e8f0"
    badge_fg: str = "#475569"
    params: list[ApiParam] = field(default_factory=list[ApiParam])
    # 요청 본문(POST 등) 필드 — $ref 스키마 property + required(스키마 `required` 배열 기준). ApiParam 재사용

    body_fields: list[ApiParam] = field(default_factory=list[ApiParam])


@dataclass
class ApiGroup:
    tag: str
    label: str
    endpoints: list[ApiEndpoint] = field(default_factory=list[ApiEndpoint])
