"""보고서 발행 service — web 측 발행·이력 단일 진실.

책임 경계:
- 비동기 발행(enqueue_report) — parent job 을 pending 으로 enqueue 후 job_id 즉시 반환(워커가 생성).
- 동기 저장(emit_report) — 완성 스냅샷을 즉시 succeeded 로 저장 (워커의 child 단일 보고서 발행 경로).
- 워커 lifecycle(claim_pending/finish_succeeded/finish_failed/recover_stale) — job 상태 전이.
- 발행 이력(list_reports) — customer + engineer 통합.
- 추상 `DiagnosticRepository`만 의존 (F4).

anchor 정규화(`normalize_anchor`)와 input_hash 계산(`compute_hash`)은 `report/result.py` 가 갖는다.
"""

from typing import TYPE_CHECKING

from loguru import logger

from assessment_engine.db.dtos.inbound import DiagnosticJobCreate

# 발행 result 조립·해시 helper 단일 진실은 report/result.py.
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
    """발행 input_params 단일 빌더 — emit_report(동기 child)·enqueue_report(비동기 parent) 동일 구조.

    input_hash 결정성 정합 의무: 두 경로가 같은 입력에 같은 hash 를 내야 멱등(active UNIQUE) 일관.
    server scope 1대는 단수 키도 — list_recent SQL 단수 매칭(이력 server 상세 link).
    """
    params: JsonObject = {
        "view": view,
        "server_public_ids": sorted(server_public_ids),
        "time_range": time_range,
        "anchor_at": anchor_at.isoformat(),
    }
    if scope == "server" and len(server_public_ids) == 1:
        params["server_public_id"] = server_public_ids[0]
    return params


class DiagnosticService:
    """web 라우터용 보고서 발행 facade — emit_report 발행 + 발행 이력."""

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
        """보고서 발행 이력 페이지(/reports/history) 용 — customer + engineer 통합. (앞 limit건, 전체 건수) 반환.

        view='all' 이면 두 job_type union (SQL 2회 + Python merge — 발행 빈도 낮아 비효율 수용).
        scope=None 전체 / 'environment' / 'selection'(server N대) / 'single'(server 1대) — record scope 기준 filter.
        selection·single 은 DB scope='server' 공유 — server_public_ids 개수로 Python 분기 (DB 컬럼만으론 구분 불가).
        total = 필터 후 전체 건수 — "더보기"(limit 누적) 카운트 "shown/total" 용.
        retention 90일 가정이라 전체 로드 수용.
        """
        # selection·single 은 DB 상 동일 scope='server' — repo 에는 'server' 로 질의 후 개수로 분리.
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
        """발행된 보고서 정적 스냅샷 단건 조회 — GET `?job={id}` 렌더용. 미존재 시 None."""
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
        """완성 스냅샷 동기 저장 — 즉시 succeeded INSERT (워커의 child 단일 보고서 발행 경로, ADR 0040).

        GET(세부·이력)은 본 job_id 의 정적 스냅샷만 렌더 (재계산 없음, 정적 보관).
        같은 input 활성 충돌(더블클릭) 시 기존 job_id 회수.

        anchor_at: 발행 시점 기준 시각 (필수) — 스냅샷 ViewModel 윈도우. 라우터가 normalize_anchor 로 분 단위 truncate.
        kind: report_result.REPORT_KIND_ENV — 모든 보고서(selection N대·환경·단일서버) 공통 양식.
        snapshot: 발행 시점 완성 ViewModel 직렬화 dict (report_serializer.*_to_dict).
        aux: ViewModel 밖 부가 정적 데이터 (운영신호 attention 등) — GET 정적 렌더가 그대로 읽음.
        child_jobs: {public_id: child_job_id} — N대 표가 발행한 개별 단일 job 맵 (세부 서버 목록 정적 link).
        """
        job_type = f"{view}_report"
        input_params = _build_input_params(view, scope, server_public_ids, time_range, anchor_at)
        input_hash = compute_hash(scope, input_params)
        result = build_report_result(kind=kind, snapshot=snapshot, view=view, aux=aux)
        if child_jobs:
            # input_hash 에는 미포함(더블클릭 dedup 보존) — result 에만 보관 (세부 서버 목록 link).
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

        같은 input 활성 충돌(더블클릭) 시 기존 active job_id 회수 — 같은 `?job={id}` 로 합류(C2 멱등).
        anchor_at 은 호출 라우터가 normalize_anchor 로 확정한 값 — 워커가 발행 윈도우 재현에 사용.
        재시도를 소진하면 ReportEnqueueError.
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
        """pending job 1건 원자적 claim(running 마킹 커밋) — 워커 루프용. 없으면 None.

        FOR UPDATE SKIP LOCKED(repo) 로 멀티워커 안전. running 마킹을 짧은 트랜잭션으로 닫아 락 즉시 해제.
        """
        async with self.session_factory() as session:
            repo = self.diagnostic_repo_factory(session)
            rec = await repo.claim_next_pending()
            await session.commit()
            return rec

    async def finish_succeeded(self, job_id: str, result: JsonObject) -> None:
        """워커가 보고서 생성 성공 시 — status=succeeded + result 스냅샷 저장."""
        async with self.session_factory() as session:
            repo = self.diagnostic_repo_factory(session)
            await repo.mark_succeeded(job_id, result)
            await session.commit()

    async def finish_failed(self, job_id: str, error_message: str) -> None:
        """워커가 생성 실패 시 — status=failed + error_message(F8 sanitize 후 전달)."""
        async with self.session_factory() as session:
            repo = self.diagnostic_repo_factory(session)
            await repo.mark_failed(job_id, error_message)
            await session.commit()

    async def recover_stale(self, stale_seconds: int) -> int:
        """워커 기동 시 1회 — 크래시/SIGTERM 으로 running 에 멈춘 job 을 pending 으로 복구. 복구 건수 반환."""
        async with self.session_factory() as session:
            repo = self.diagnostic_repo_factory(session)
            n = await repo.recover_stale_running(stale_seconds)
            await session.commit()
            return n
