"""Assessment API router — /api/assessment 통합 프로비저닝 어세스먼트.

계약 단일 진실: docs/reference/contracts/assessment-api.md. 재해복구/마이그레이션 에이전트가 타겟 VM 을 재현(소스
레이아웃 그대로) 또는 수정 사이징(관측 부하 맞춤)으로 생성하는 데 필요한 것(identity/reproduction/sizing/
assessment/diagnostics)을 한 응답으로. 매칭(hostname/ip/public_id/pair)/윈도우/안전경고는 right-sizing 과 동일.
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Query

from assessment_engine.web.deps import QueryServiceDep
from assessment_engine.web.routers._contract_params import (
    WINDOW_FLOOR_DAYS,
    normalize_anchor,
    split_csv,
    split_pairs_csv,
)
from assessment_engine.web.services.mappers.assessment_api import build_assessment_envelope
from assessment_engine.web.view_models.assessment_api import AssessmentEnvelope

assessment_router = APIRouter(prefix="/api/assessment", tags=["assessment"])


# responses= (response_model 아님) — OpenAPI 스키마만 문서화(생성 TS 타입 원천). frozen 계약 출력은
# 매퍼 dict 그대로, 재구성/검증 안 함. 매퍼-스키마 drift 는 test_assessment_api_schema 가 골든으로 잡음.
@assessment_router.get("", responses={200: {"model": AssessmentEnvelope}})
async def get_assessment(
    service: QueryServiceDep,
    hostname: Annotated[
        str | None, Query(description="쉼표 구분 호스트명(정확 매칭, 대소문자 구분). 미지정 시 전체.")
    ] = None,
    ip: Annotated[str | None, Query(description="쉼표 구분 IPv4 — 물리 인터페이스 매칭.")] = None,
    pair: Annotated[
        str | None,
        Query(
            description="쉼표 구분 순서쌍 hostname~discriminator (discriminator=IP 또는 public_id). "
            "호스트명 중복 시 host AND 판별자 동시 만족 서버만 — 정확 지정."
        ),
    ] = None,
    public_id: Annotated[str | None, Query(description="쉼표 구분 서버 public_id(UUID) — 있으면 유일 지정.")] = None,
    window_days: Annotated[
        int,
        Query(
            ge=WINDOW_FLOOR_DAYS,
            le=90,
            description="평가 창(일). 최소 14(계약 3절 — 짧으면 관측 부족 과소 사이징). 연장만 안전 방향.",
        ),
    ] = WINDOW_FLOOR_DAYS,
    end: Annotated[datetime | None, Query(description="윈도우 종료 시각(ISO 8601, tz-aware 권장). 기본 현재.")] = None,
):
    """통합 프로비저닝 어세스먼트 — 소스 서버를 관측해 타겟 VM 재현/수정 사이징에 필요한 것을 한 응답으로.

    소비자(재해복구/마이그레이션 에이전트)는 자기 인벤토리가 아는 hostname/ip 로 고른다(내부 public_id 몰라도 됨,
    알면 유일 지정). 미지정 시 전체 서버. window_days(기본 14일) 종료 end(기본 현재) 창으로 평가하며 데이터가
    창보다 짧아도 판정은 완결되고 부족분은 assessment.data_quality 로 신뢰도 하향된다.

    응답(계약 4절): 최상위 envelope(contract_version/generated_at/window/filter/warnings/count) + servers[].
    각 서버 = identity / reproduction(os/network/storage 재현 팩트) / sizing(axes[] — cpu/memory/디스크 마운트별,
    current+recommended+action+estimate_quality) / assessment(분류/신뢰도) / diagnostics(근본원인/근거, 선택 소비).

    안전(호스트명 중복): hostname 은 유일하지 않다. public_id 를 모를 때 중복 hostname 을 하나로 지정하려면
    pair=hostname~ip 로 host AND ip 동시 만족 서버만 고른다. warnings 가 동명 충돌/해석 실패/미매칭 필터를 알려준다.
    """
    anchor = normalize_anchor(end)
    hostnames = split_csv(hostname)
    ips = split_csv(ip)
    public_ids = split_csv(public_id)
    pairs = split_pairs_csv(pair)
    result = await service.get_assessment(
        window_days=window_days,
        end=anchor,
        hostnames=hostnames,
        ips=ips,
        public_ids=public_ids,
        pairs=pairs,
    )
    return build_assessment_envelope(
        result,
        generated_at=datetime.now(UTC).isoformat(),
        window_days=window_days,
        window_start=(anchor - timedelta(days=window_days)).isoformat(),
        window_end=anchor.isoformat(),
        filters={
            "hostname": hostnames,
            "ip": ips,
            "public_id": public_ids,
            "pair": [f"{h}~{d}" for h, d in pairs],
        },
    )
