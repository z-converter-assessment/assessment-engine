"""Right-sizing API router — 외부/자동화 소비용 자원 적정성 판정.

화면(보고서·자원 평가)과 동일 산식(get_report_aggregate -> rollup_host) — 값 정합(재계산 0). 자원 3축
(CPU·메모리·디스크) 사이징 + 네트워크 품질(별도 플래그). 분류·근본원인·근거·신뢰도·권고를 파싱 쉬운
JSON 으로 노출해 "이렇게 프로비저닝하면 된다"를 받는 쪽이 바로 소비하게 한다.
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Query

from assessment_engine.json_types import JsonObject
from assessment_engine.web.deps import QueryServiceDep
from assessment_engine.web.routers._contract_params import (
    normalize_anchor,
    split_csv,
    split_pairs_csv,
)
from assessment_engine.web.view_models.right_sizing_api import RightSizingResponse

right_sizing_router = APIRouter(prefix="/api/right-sizing", tags=["right-sizing"])


# responses= (response_model 아님) — OpenAPI 스키마만 문서화(생성 TS 타입 원천). frozen 계약 출력은 매퍼
# dict 그대로, 재구성/검증 0. 매퍼-스키마 drift 는 test_right_sizing_api 골든 검증이 잡음.
@right_sizing_router.get("", responses={200: {"model": RightSizingResponse}})
async def get_right_sizing(
    service: QueryServiceDep,
    hostname: Annotated[
        str | None, Query(description="쉼표 구분 호스트명(정확 매칭·대소문자 구분). 미지정 시 전체.")
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
    window_days: Annotated[int, Query(ge=1, le=30, description="평가 윈도우(일). 기본 14 = 자원 적정성 표준 창.")] = 14,
    end: Annotated[datetime | None, Query(description="윈도우 종료 시각(ISO 8601, tz-aware 권장). 기본 현재.")] = None,
) -> JsonObject:
    """서버 자원 적정성 판정 (프로비저닝 가이드) — CPU·메모리·디스크 3축 사이징 + 네트워크 품질.

    외부 자동화가 소비하는 API라 자기 인벤토리가 아는 hostname/ip 로 고른다(내부 public_id 는 몰라도 됨 — 알면
    public_id 로 유일 지정 가능). hostname/ip/public_id/pair 중 하나라도 매칭이면 포함, 미지정 시 전체 서버.
    window_days(기본 14일) 종료 end(기본 현재) 창으로 평가하며, 데이터가 창보다 짧아도 판정은 완결되고 부족분은
    각 자원 confidence_notes("표본 부족")로 자동 하향된다.

    응답(servers[]) 서버별 필드:
    - classification / classification_label: 호스트 종합 분류(자원 부족·과다·유휴·적정·표본 부족).
    - root_cause: 근본원인(인과 결합 시 "메모리 (CPU 유발)" 형태).
    - recommendation: 종합 권고 구조 {summary, kind, actions[], suppressed[]} — actions 만 파싱해 실행(관측된
      under 자원 전부, 자원별 독립). suppressed 는 항상 빈 배열(구 스키마 호환 필드). root_cause 가 인과 근거.
    - confidence_notes: 신뢰도 하향 사유(표본 부족·포화 수치 미관측 등).
    - resources.cpu / .memory: status·이용률 p95·saturation(OS별 신호·값·임계·포화)·evidence·사이징 목표.
    - resources.disk.capacity: 소진 임박(worst mount·잔여일)·사이징 목표(GB). .disk.io: await 포화.
    - network: 사이징 아닌 품질(혼잡/정상/미관측) — 별도 축.

    안전(호스트명 중복): hostname 은 유일하지 않다. 외부에서 public_id 를 모를 때 중복 hostname 을 정확히 하나로
    지정하려면 순서쌍 pair=hostname~ip(자기가 아는 IP) 로 host AND ip 동시 만족 서버만 고른다. 서버가 충돌을 검사해
    각 서버 hostname_ambiguous(동명 2대+ 공유)·warnings.ambiguous_hostnames(중복명 필터 명중)·unresolved_pairs
    (해석 실패 순서쌍)를 함께 돌려준다.

    페이지네이션: 목록 endpoint 이나 page/cursor 없이 매칭 전량 반환 — 외부 자동화가 fleet 를 한 번에 소비하는
    목적의 의식적 결정(#E2 이탈, export 규약과 동종). 대규모 인벤토리는 ids/hostname 으로 스코프해 호출 비용을 줄인다.
    """
    anchor = normalize_anchor(end)
    hostnames = split_csv(hostname)
    ips = split_csv(ip)
    public_ids = split_csv(public_id)
    pairs = split_pairs_csv(pair)
    result = await service.get_right_sizing(
        window_days=window_days,
        end=anchor,
        hostnames=hostnames,
        ips=ips,
        public_ids=public_ids,
        pairs=pairs,
    )
    servers = result["servers"]
    now = datetime.now(UTC)
    return {
        "right_sizing": {
            "engine_id": "zconverter-assessment-portal",
            "generated_at": now.isoformat(),
            "window": {
                "days": window_days,
                "start": (anchor - timedelta(days=window_days)).isoformat(),
                "end": anchor.isoformat(),
                "basis": "자원 적정성 표준 창(기본 14일). 데이터가 창보다 짧으면 confidence_notes 로 신뢰도 하향.",
            },
            "filter": {
                "hostname": hostnames,
                "ip": ips,
                "public_id": public_ids,
                "pair": [f"{h}~{d}" for h, d in pairs],
            },
            "warnings": {
                "ambiguous_hostnames": result["ambiguous_hostnames"],
                "unresolved_pairs": result["unresolved_pairs"],
            },
            "count": len(servers),
            "servers": servers,
        }
    }
