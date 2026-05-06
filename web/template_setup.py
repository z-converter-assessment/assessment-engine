"""Jinja2 템플릿 환경 단일 인스턴스.

라우터(`pages.py`)에 직접 인스턴스를 두면 라우터가 표시 셋업 책임까지 떠안게 된다.
filter 등록을 한 곳으로 모아 다른 라우터/모듈도 동일 인스턴스를 import할 수 있게 한다.
"""
from pathlib import Path

from fastapi.templating import Jinja2Templates

from web.template_filters import disksize, kbps, kst, or_dash, service_badge_class

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.filters["kst"]                 = kst
templates.env.filters["disksize"]            = disksize
templates.env.filters["kbps"]                = kbps
templates.env.filters["service_badge_class"] = service_badge_class
templates.env.filters["or_dash"]             = or_dash