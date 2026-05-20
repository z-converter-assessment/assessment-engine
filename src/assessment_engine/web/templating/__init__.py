"""Jinja2 템플릿 인프라 sub-package.

`templates` (Jinja2Templates 단일 인스턴스) 를 caller (라우터) 가 본 sub-package 에서 import.
filter 정의·환경 셋업은 `setup.py` 단일 진실.
"""

from assessment_engine.web.templating.setup import ASSET_V, templates

__all__ = ["ASSET_V", "templates"]
