"""보고서 발행 service — web 측 발행·이력 단일 진실.

anchor 정규화(`normalize_anchor`)와 input_hash 계산(`compute_hash`)은 `report/result.py` 가 갖는다.
"""

from typing import TYPE_CHECKING

from loguru import logger

from assessment_engine.db.dtos.inbound import DiagnosticJobCreate
from assessment_engine.web.services.report import build_report_result, compute_hash

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from assessment_engine.db.dtos.outbound import DiagnosticJobRecord
    from assessment_engine.db.repositories.diagnostic import (
        DiagnosticRepository,
    )
    from assessment_engine.json_types import JsonObject

_ENQUEUE_MAX_ATTEMPTS = 2


class ReportEnqueueError(Exception):
    """활성 job 과 충돌했는데 회수할 job 도 없다 — 재시도 소진."""


def _build_input_params(
    view: str, scope: str, server_public_ids: list[str], time_range: str, anchor_at: datetime
) -> JsonObject:
    """발행 input_params 단일 빌더.

    emit_report(동기 child)와 enqueue_report(비동기 parent)가 같은 입력에 같은 input_hash 를 내야
    active UNIQUE 멱등이 성립하므로 구조를 한 곳에서 만든다.
    """
    params: JsonObject = {
        "view": view,
        "server_public_ids": sorted(server_public_ids),
        "time_range": time_range,
        "anchor_at": anchor_at.isoformat(),
    }
    if scope == "server" and len(server_public_ids) == 1:
        params["server_public_id"] = server_public_ids[0]  # 이력 SQL 이 단수 키로 server 상세와 매칭한다.
    return params


class DiagnosticService:
    """보고서 발행·이력·job 상태 전이 facade — 추상 `DiagnosticRepository` 만 의존."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        diagnostic_repo_factory: Callable[[AsyncSession], DiagnosticRepository],
    ):
        self.session_factory = session_factory
        self.diagnostic_repo_factory = diagnostic_repo_factory

    async def list_reports(
        self,
        days: int,
        view: str = "all",
        server_public_ids: list[str] | None = None,
        limit: int = 20,
        scope: str | None = None,
    ) -> tuple[list[DiagnosticJobRecord], int]:
        """보고서 발행 이력 — (앞 limit 건, 필터 후 전체 건수).

        scope: None 전체 / 'environment' / 'selection'(server N대) / 'single'(server 1대).
        view='all' 은 두 job_type 을 SQL 2회 + Python merge 로 합친다 — 발행 빈도가 낮아 수용.
        전체 건수를 위해 필터 결과를 다 로드하는 것도 retention 90일 가정에서 수용.
        """
        # selection(N대)·single 은 DB 상 같은 scope='server' 라 컬럼만으론 못 가른다 — 질의 후 개수로 분리.
        repo_scope = "server" if scope in ("selection", "single") else scope
        async with self.session_factory() as session:
            repo = self.diagnostic_repo_factory(session)
            if view == "all":
                customer = await repo.list_recent(days, repo_scope, server_public_ids, "customer_report")
                engineer = await repo.list_recent(days, repo_scope, server_public_ids, "engineer_report")
                merged = customer + engineer
            else:
                job_type = f"{view}_report"
                merged = await repo.list_recent(days, repo_scope, server_public_ids, job_type)
            if scope == "selection":
                merged = [r for r in merged if len(r.input_params.get("server_public_ids", [])) >= 2]
            elif scope == "single":
                merged = [r for r in merged if len(r.input_params.get("server_public_ids", [])) == 1]
            merged.sort(key=lambda r: r.created_at, reverse=True)
            return merged[:limit], len(merged)

    async def get_report_snapshot(self, job_id: str) -> DiagnosticJobRecord | None:
        """발행된 보고서 정적 스냅샷 단건. 미존재 시 None."""
        async with self.session_factory() as session:
            repo = self.diagnostic_repo_factory(session)
            return await repo.get_by_id(job_id)

    async def emit_report(
        self,
        *,
        view: str,
        scope: str,
        kind: str,
        snapshot: JsonObject,
        server_public_ids: list[str],
        time_range: str,
        anchor_at: datetime,
        aux: JsonObject | None = None,
        child_jobs: JsonObject | None = None,
        requested_by: str | None = None,
    ) -> str | None:
        """완성 스냅샷 동기 저장 — 즉시 succeeded INSERT. 활성 충돌 시 기존 job_id 회수.

        anchor_at: 스냅샷 윈도우 기준 시각. 라우터가 normalize_anchor 로 분 단위 truncate 한 값.
        kind: `report_result.REPORT_KIND_ENV` — selection N대·환경·단일서버가 한 양식을 공유한다.
        snapshot: 발행 시점 완성 ViewModel 직렬화 dict (`report_serializer.*_to_dict`).
        aux: ViewModel 밖 부가 정적 데이터 — GET 정적 렌더가 그대로 읽는다.
        child_jobs: {public_id: child_job_id} — N대 표가 발행한 개별 단일 job 맵.
        """
        job_type = f"{view}_report"
        input_params = _build_input_params(view, scope, server_public_ids, time_range, anchor_at)
        input_hash = compute_hash(scope, input_params)
        result = build_report_result(kind=kind, snapshot=snapshot, view=view, aux=aux)
        if child_jobs:
            # input_hash 에는 넣지 않는다 — 넣으면 child 구성이 달라질 때 더블클릭 dedup 이 깨진다.
            result["child_jobs"] = child_jobs
        async with self.session_factory() as session:
            repo = self.diagnostic_repo_factory(session)
            new_id = await repo.enqueue(
                DiagnosticJobCreate(
                    scope=scope,
                    job_type=job_type,
                    input_params=input_params,
                    input_hash=input_hash,
                    requested_by=requested_by,
                )
            )
            if new_id is None:
                active_id = await repo.get_active_by_hash(scope, input_hash, job_type)
                await session.rollback()
                logger.info("report emit active conflict view={} scope={} hash={}", view, scope, input_hash[:12])
                return active_id
            await repo.mark_succeeded(new_id, result)
            await session.commit()
        logger.info("report emitted view={} scope={} job_id={} status=succeeded", view, scope, new_id)
        return new_id

    async def enqueue_report(
        self,
        *,
        view: str,
        scope: str,
        server_public_ids: list[str],
        time_range: str,
        anchor_at: datetime,
        requested_by: str | None = None,
    ) -> str:
        """비동기 발행 — parent job 을 pending 으로 enqueue 후 job_id 즉시 반환(워커가 생성·저장).

        같은 input 활성 충돌(더블클릭) 시 기존 active job_id 를 회수해 같은 job 으로 합류시킨다.
        재시도를 소진하면 `ReportEnqueueError`.
        """
        job_type = f"{view}_report"
        input_params = _build_input_params(view, scope, server_public_ids, time_range, anchor_at)
        input_hash = compute_hash(scope, input_params)
        # INSERT 가 충돌로 비고 회수 SELECT 까지 비는 경우는 둘이다 — 두 문장 사이에서 그 job 이 끝났거나,
        # 동시 INSERT 가 아직 커밋 전이라 READ COMMITTED SELECT 에 안 보이거나. 앞의 경우는 다시 INSERT 하면
        # 자리를 잡고, 뒤의 경우는 재시도가 상대의 커밋을 만나 회수로 이어진다.
        for _ in range(_ENQUEUE_MAX_ATTEMPTS):
            async with self.session_factory() as session:
                repo = self.diagnostic_repo_factory(session)
                new_id = await repo.enqueue(
                    DiagnosticJobCreate(
                        scope=scope,
                        job_type=job_type,
                        input_params=input_params,
                        input_hash=input_hash,
                        requested_by=requested_by,
                    )
                )
                if new_id is not None:
                    await session.commit()
                    logger.info("report enqueued view={} scope={} job_id={} status=pending", view, scope, new_id)
                    return new_id
                active_id = await repo.get_active_by_hash(scope, input_hash, job_type)
                await session.rollback()
            if active_id is not None:
                logger.info("report enqueue active conflict view={} scope={} hash={}", view, scope, input_hash[:12])
                return active_id
        raise ReportEnqueueError(f"enqueue conflict unresolved scope={scope} job_type={job_type}")

    async def claim_pending(self) -> DiagnosticJobRecord | None:
        """pending job 1건 원자적 claim(running 마킹 커밋). 없으면 None.

        멀티워커가 같은 job 을 집지 않게 repo 가 FOR UPDATE SKIP LOCKED 로 뽑고,
        running 마킹을 짧은 트랜잭션으로 닫아 락을 바로 푼다.
        """
        async with self.session_factory() as session:
            repo = self.diagnostic_repo_factory(session)
            rec = await repo.claim_next_pending()
            await session.commit()
            return rec

    async def finish_succeeded(self, job_id: str, result: JsonObject) -> None:
        """생성 성공 — status=succeeded + result 스냅샷 저장."""
        async with self.session_factory() as session:
            repo = self.diagnostic_repo_factory(session)
            await repo.mark_succeeded(job_id, result)
            await session.commit()

    async def finish_failed(self, job_id: str, error_message: str) -> None:
        """생성 실패 — status=failed + error_message. 호출자가 sanitize 한 문자열을 넘긴다."""
        async with self.session_factory() as session:
            repo = self.diagnostic_repo_factory(session)
            await repo.mark_failed(job_id, error_message)
            await session.commit()

    async def recover_stale(self, stale_seconds: int) -> int:
        """크래시·SIGTERM 으로 running 에 멈춘 job 을 pending 으로 되돌린다. 복구 건수 반환."""
        async with self.session_factory() as session:
            repo = self.diagnostic_repo_factory(session)
            n = await repo.recover_stale_running(stale_seconds)
            await session.commit()
            return n
