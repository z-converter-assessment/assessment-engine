"""발행된 보고서 스냅샷 로딩 — 환경·selection·단일 세 라우터 공용.

세 화면이 같은 시퀀스를 타는데 라우터마다 복제해 두면 404 문구나 kind 게이트 하나가 어긋나도 아무도 모른다.

컨텍스트 조립은 여기서 하지 않는다 — 세 화면이 서로 다른 키를 넣고(환경만 `active_nav`) 합치면
사이드바 하이라이트가 바뀐다.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from fastapi import HTTPException, Request

from assessment_engine.db.dtos.outbound import DiagnosticJobRecord
from assessment_engine.db.repositories.query.types import DIAGNOSTIC_DEFAULT_TIME_RANGE
from assessment_engine.json_types import JsonObject
from assessment_engine.web.services.report import REPORT_KIND_ENV, env_report_from_dict
from assessment_engine.web.templating import templates
from assessment_engine.web.view_models.environment_report import EnvironmentReportSummary

if TYPE_CHECKING:
    from starlette.responses import HTMLResponse

    from assessment_engine.web.services.diagnostic_service import DiagnosticService

VIEW_TITLES: dict[str, str] = {
    "customer": "고객 제출용",
    "engineer": "엔지니어 검토용",
}


@dataclass(frozen=True, slots=True)
class LoadedSnapshot:
    """succeeded 스냅샷 1건 — 라우터가 컨텍스트를 조립할 재료."""

    record: DiagnosticJobRecord
    summary: EnvironmentReportSummary
    view: str
    time_range: str
    result: JsonObject

    @property
    def view_title(self) -> str:
        return VIEW_TITLES.get(self.view, self.view)

    def aux(self, key: str) -> JsonObject:
        """발행 시점에 함께 저장한 부수 데이터 — 없으면 빈 dict."""
        return self.result.get("aux", {}).get(key, {})


async def load_snapshot(job_id: str, diag_service: DiagnosticService) -> LoadedSnapshot | DiagnosticJobRecord:
    """스냅샷을 읽는다 — 아직 succeeded 가 아니면 진행 화면용으로 job 레코드를 그대로 돌려준다.

    호출자는 반환 타입으로 분기한다. 진행 화면 렌더는 라우터가 갖는다.
    """
    rec = await diag_service.get_report_snapshot(job_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="report snapshot not found")
    if rec.status != "succeeded":
        return rec
    if rec.result is None or rec.result.get("kind") != REPORT_KIND_ENV:
        raise HTTPException(status_code=404, detail="report snapshot not found")
    result = rec.result
    return LoadedSnapshot(
        record=rec,
        summary=env_report_from_dict(result["snapshot"]),
        view=result.get("view", "engineer"),
        time_range=rec.input_params.get("time_range", DIAGNOSTIC_DEFAULT_TIME_RANGE),
        result=result,
    )


def render_job_progress(
    request: Request,
    rec: DiagnosticJobRecord,
    back_url: str,
) -> HTMLResponse:
    """job 이 succeeded 아닐 때의 화면 — pending/running 은 report-poll.js 가 폴링, failed 는 재발행 안내."""
    return templates.TemplateResponse(
        request=request,
        name="reports/report_pending.html",
        context={
            "job_id": rec.id,
            "status": rec.status,
            "error": rec.error_message if rec.status == "failed" else None,
            "back_url": back_url,
        },
    )
