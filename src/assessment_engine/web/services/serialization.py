"""ViewModel <-> JSON 직렬화 공용 유틸 (중립) — cache_serializer·report_serializer 공유.

직렬화 계약 단일 진실: datetime -> ISO 문자열 / dataclass -> JsonObject(asdict). 캐시 스냅샷과 보고서 정적
스냅샷이 동일 규칙을 쓰도록 한 곳에서 정의 (각 serializer 가 byte-identical 헬퍼를 복제하지 않음).
"""

import dataclasses
import json
from datetime import datetime
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from _typeshed import DataclassInstance

    from assessment_engine.json_types import JsonObject


def json_default(obj: object) -> str:
    """json.dumps default — datetime 만 ISO 문자열로. 그 외 타입은 TypeError(직렬화 계약 밖)."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Cannot serialize {type(obj)}")


def _jsonable(value: object) -> object:
    """JSON 표현으로 낮춘다 — `json.dumps` 가 하는 변환을 그대로 따른다.

    tuple -> list, 비문자열 dict 키 -> str 은 인코더의 동작이라 여기서도 그대로 재현해야 한다.
    저장된 JSONB 와 캐시 값이 바이트 동일이어야 하므로 "더 나은 표현" 을 고르지 않는다.
    """
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        items = cast("dict[object, object]", value).items()
        return {(k if isinstance(k, str) else json.dumps(k)): _jsonable(v) for k, v in items}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in cast("list[object]", value)]
    return value


def to_jsonable(vm: DataclassInstance) -> JsonObject:
    """dataclass ViewModel -> JSONB 저장 가능 dict (datetime -> ISO str, nested 재귀).

    `asdict` 가 이미 트리를 깊은 복사하므로 그 위를 한 번 더 걷는다 — datetime 하나를 ISO 로 바꾸려고
    트리 전체를 JSON 문자열로 인코딩했다 다시 파싱할 이유가 없다. 환경 보고서는 서버 N대 트리 전체가
    발행 때마다 이 경로를 탄다.
    """
    return cast("JsonObject", _jsonable(dataclasses.asdict(vm)))


def parse_dt(v: str | datetime | None) -> datetime | None:
    """ISO 문자열 -> datetime (역직렬화). 이미 datetime/None 이면 그대로."""
    return datetime.fromisoformat(v) if isinstance(v, str) else v
