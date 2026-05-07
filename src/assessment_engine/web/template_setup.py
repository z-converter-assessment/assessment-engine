"""Jinja2 템플릿 환경 단일 인스턴스.

라우터(`pages.py`)에 직접 인스턴스를 두면 라우터가 표시 셋업 책임까지 떠안게 된다.
filter 등록을 한 곳으로 모아 다른 라우터/모듈도 동일 인스턴스를 import할 수 있게 한다.
"""
from pathlib import Path

from fastapi.templating import Jinja2Templates
from jinja2 import Environment

from assessment_engine.web.template_filters import disksize, kbps, kst, or_dash, service_badge_class

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# starlette type stub은 Jinja2Templates.env를 Optional로 정의하지만 실제로는 항상 Environment.
# false positive — 변수로 받아 한 곳에서만 specific ignore (이후 라인들은 narrow된 env 사용).
env: Environment = templates.env  # type: ignore[assignment]
env.filters["kst"]                 = kst
env.filters["disksize"]            = disksize
env.filters["kbps"]                = kbps
env.filters["service_badge_class"] = service_badge_class
env.filters["or_dash"]             = or_dash