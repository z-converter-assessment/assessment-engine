"""SSR partial 재렌더 스위치 — `?fragment=<name>`.

세 페이지가 같은 스위치를 각자 `str | None` 으로 받아 본문에서 문자열을 비교하고 있었다. 여기 별칭을
쓰면 허용값이 시그니처와 OpenAPI 스키마에 드러난다.

허용값 외 입력은 422 가 아니라 None 으로 낮춘다 — `?fragment=아무거나` 가 full page 200 을 내는 것이
현행 동작이고, 배포된 URL 을 422 로 바꾸지 않는다. 결과적으로 스키마(enum)가 서버가 실제로 받는
범위보다 좁다.
"""

from typing import Annotated, Literal

from fastapi import Query
from pydantic import BeforeValidator


def _only(name: str):
    """`name` 만 통과시키고 나머지는 None. 라우터 본문의 문자열 비교를 시그니처로 올린다."""

    def _coerce(value: object) -> str | None:
        return name if value == name else None

    return BeforeValidator(_coerce)


_DESCRIPTION = "partial 재렌더 스위치 — 지정 시 해당 조각만 반환. 그 외 값은 full page"

type RealtimeFragment = Annotated[
    Literal["realtime"] | None,
    _only("realtime"),
    Query(description=_DESCRIPTION),
]

type ResultFragment = Annotated[
    Literal["result"] | None,
    _only("result"),
    Query(description=_DESCRIPTION),
]

type RowsFragment = Annotated[
    Literal["rows"] | None,
    _only("rows"),
    Query(description=_DESCRIPTION),
]
