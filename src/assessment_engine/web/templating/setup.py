"""Jinja2 템플릿 환경 단일 인스턴스.

라우터(`pages.py`)에 직접 인스턴스를 두면 라우터가 표시 셋업 책임까지 떠안게 된다.
filter 등록을 한 곳으로 모아 다른 라우터/모듈도 동일 인스턴스를 import할 수 있게 한다.
"""

import time
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from fastapi.templating import Jinja2Templates

from assessment_engine.db.repositories.query.types import (
    DIAGNOSTIC_DEFAULT_TIME_RANGE,
)
from assessment_engine.web.services.mappers.constants import (
    _USAGE_DANGER_PCT,
    _USAGE_WARN_PCT,
    DIAGNOSTIC_RANGE_LABEL_KR,
)
from assessment_engine.web.templating.filters import (
    disksize,
    disksize_styled,
    kst,
    or_dash,
    service_badge_class,
    storagesize,
    storagesize_styled,
)

if TYPE_CHECKING:
    from jinja2 import Environment

templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))

env: Environment = templates.env
# jinja2 는 globals·filters 의 값 타입을 우리가 볼 수 있는 형태로 주지 않는다. 실제 계약은 임의 값이라

env_globals = cast("dict[str, Any]", env.globals)
env_filters = cast("dict[str, Any]", env.filters)
env_filters["kst"] = kst
env_filters["disksize"] = disksize
env_filters["storagesize"] = storagesize
env_filters["disksize_styled"] = disksize_styled
env_filters["storagesize_styled"] = storagesize_styled
env_filters["service_badge_class"] = service_badge_class
env_filters["or_dash"] = or_dash


ASSET_V: str = format(int(time.time()), "x")
env_globals["asset_v"] = ASSET_V


# 엔진(포털) 버전 — 전역 상단 바에 노출. 설치 메타데이터의 버전. 미설치/개발 트리면 "dev".
def _engine_version() -> str:
    try:
        return version("assessment-engine")
    except PackageNotFoundError:
        return "dev"


ENGINE_VERSION: str = _engine_version()
env_globals["engine_version"] = ENGINE_VERSION


# 진단 카드 자동 발행 안내 문구에서 노출 — 상수 변경 시 라벨도 자동 갱신.
env_globals["diagnostic_default_range_label"] = DIAGNOSTIC_RANGE_LABEL_KR.get(
    DIAGNOSTIC_DEFAULT_TIME_RANGE,
    DIAGNOSTIC_DEFAULT_TIME_RANGE,
)

# UI badge 임계값 — mappers 단일 진실 (#E3 UI badge 도메인). base.html body data-attribute 로 노출,

env_globals["ui_thresholds"] = {
    "usage_danger_pct": _USAGE_DANGER_PCT,
    "usage_warn_pct": _USAGE_WARN_PCT,
}


NAV_GROUPS: list[dict[str, Any]] = [
    {
        "label": "모니터링",
        "links": [
            {"label": "환경 개요", "href": "/", "match": "overview"},
            {"label": "서버 목록", "href": "/servers", "match": "list"},
            {"label": "환경 자원 평가", "href": "/environment/assessment", "match": "assessment"},
            {"label": "네트워크 토폴로지", "href": "/environment/topology", "match": "topology"},
            {"label": "실시간 현황", "href": "/environment/realtime", "match": "realtime"},
            {"label": "환경 성능 추이", "href": "/environment/metrics", "match": "performance"},
        ],
    },
    {
        "label": "보고서",
        "links": [
            {"label": "환경 보고서", "href": "/reports/environment", "match": "environment"},
            {"label": "발행 이력", "href": "/reports/history", "match": "history"},
        ],
    },
    {
        "label": "참고",
        "links": [
            {"label": "지표·기준", "href": "/reference", "match": "thresholds"},
            {"label": "API 목록", "href": "/reference/api", "match": "api"},
        ],
    },
]
env_globals["nav_groups"] = NAV_GROUPS


env_globals["nav_breadcrumb"] = {
    link["match"]: {"group": group["label"], "item": link["label"]} for group in NAV_GROUPS for link in group["links"]
}
env_globals["active_nav"] = None
