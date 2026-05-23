"""Jinja2 템플릿 환경 단일 인스턴스.

라우터(`pages.py`)에 직접 인스턴스를 두면 라우터가 표시 셋업 책임까지 떠안게 된다.
filter 등록을 한 곳으로 모아 다른 라우터/모듈도 동일 인스턴스를 import할 수 있게 한다.
"""

import time
from pathlib import Path

from fastapi.templating import Jinja2Templates
from jinja2 import Environment

from assessment_engine.db.repositories.base_diagnostic_repository import (
    DIAGNOSTIC_DEFAULT_TIME_RANGE,
    DIAGNOSTIC_RANGE_LABEL_KR,
)
from assessment_engine.web.services.mappers.shared import (
    _SWAP_DANGER_PCT,
    _USAGE_DANGER_PCT,
    _USAGE_WARN_PCT,
)
from assessment_engine.web.settings import diagnostic_settings
from assessment_engine.web.templating.filters import disksize, kbps, kst, or_dash, service_badge_class

templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))

# starlette type stub은 Jinja2Templates.env를 Optional로 정의하지만 실제로는 항상 Environment.
# false positive — 변수로 받아 한 곳에서만 specific ignore (이후 라인들은 narrow된 env 사용).
env: Environment = templates.env  # type: ignore[assignment]
env.filters["kst"] = kst
env.filters["disksize"] = disksize
env.filters["kbps"] = kbps
env.filters["service_badge_class"] = service_badge_class
env.filters["or_dash"] = or_dash

# Static asset versioning — process startup time hex를 모든 페이지 static URL의 querystring에 부착.
# 코드 변경 후 web 재시작 → 새 token → 브라우저가 새 URL로 인식 → 강제 재다운로드.
# dev/staging/prod 동일 패턴. 정식 deploy에는 commit hash 등으로 대체 가능 — 그때는 ASSET_V 갱신.
ASSET_V: str = format(int(time.time()), "x")
env.globals["asset_v"] = ASSET_V

# 스케줄러 자동 발행 기본 기간 라벨 — F10 단일 진실 (recommendation.WINDOW_DAYS 와 정합).
# 진단 카드 자동 발행 안내 문구에서 노출 — 상수 변경 시 라벨도 자동 갱신.
env.globals["diagnostic_default_range_label"] = DIAGNOSTIC_RANGE_LABEL_KR.get(
    DIAGNOSTIC_DEFAULT_TIME_RANGE,
    DIAGNOSTIC_DEFAULT_TIME_RANGE,
)

# UI badge 임계값 — mappers 단일 진실 (#E3 UI badge 도메인). base.html body data-attribute 로 노출,
# detail.js / performance.js 가 dataset 에서 읽기 (#E1 P4 — JS 임계 분류 단일 진실).
env.globals["ui_thresholds"] = {
    "usage_danger_pct": _USAGE_DANGER_PCT,
    "usage_warn_pct": _USAGE_WARN_PCT,
    "swap_danger_pct": _SWAP_DANGER_PCT,
}

# 진단 기능 활성 여부 — template 안 `{% if diagnostic_enabled %}` 분기로 진단 전용 JS load 조건부.
# DIAGNOSTIC_ENABLED=false 시 diagnostic.js (282 lines) / diagnostic-view-toggle.js 등 dead load 회피.
env.globals["diagnostic_enabled"] = diagnostic_settings.diagnostic_enabled
