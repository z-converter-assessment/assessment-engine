"""SSR 페이지 라우터 — URL 명사 분리 (환경 단위 `/`·`/environment/*`, 서버 단위 `/servers/*`, 보고서 `/reports/*`).

각 sub-router 가 자체 prefix 를 가져 `pages_router` 는 묶음만(추가 prefix 없음):
- `list_page.py`  — 개요(`/`) · 서버 목록(`/servers`) · 환경 단위(`/environment/{assessment,realtime,metrics,topology}`)
- `server_detail.py` — 상세 5 탭 + storage/network (`/servers/{id}/*`)
- `report_page.py` — 단일 보고서(`/servers/{id}/report`) · 선택 N대 보고서(`/reports/servers`)
"""

from fastapi import APIRouter

from assessment_engine.web.routers.pages.list_page import (
    environment_router,
    overview_router,
    servers_list_router,
)
from assessment_engine.web.routers.pages.report_page import report_multi_router, report_single_router
from assessment_engine.web.routers.pages.server_detail import server_detail_router

pages_router = APIRouter(tags=["pages"])
pages_router.include_router(overview_router)
pages_router.include_router(servers_list_router)
pages_router.include_router(environment_router)
pages_router.include_router(server_detail_router)
pages_router.include_router(report_single_router)
pages_router.include_router(report_multi_router)
