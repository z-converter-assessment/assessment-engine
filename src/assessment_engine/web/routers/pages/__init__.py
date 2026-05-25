"""SSR 페이지 라우터 — 도메인별 sub-module 합성.

`pages_router` 단일 prefix `/servers` 아래 3 도메인 sub-router 결합:
- `list_page.py`  — 목록 (/servers/, 환경 요약·신호·다중 액션 모달)
- `server_detail.py` — 상세 5 탭 (cpu/memory/services/performance/detail) + storage/network
- `report_page.py` — 보고서 (/servers/report 다중, /servers/{id}/report 단일)
"""

from fastapi import APIRouter

from assessment_engine.web.routers.pages.list_page import list_page_router
from assessment_engine.web.routers.pages.report_page import report_page_router
from assessment_engine.web.routers.pages.server_detail import server_detail_router

pages_router = APIRouter(prefix="/servers", tags=["pages"])
pages_router.include_router(list_page_router)
pages_router.include_router(report_page_router)
pages_router.include_router(server_detail_router)
