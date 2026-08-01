"""ViewModel <-> JSON 직렬화 공용 유틸 (중립) — cache_serializer·report_serializer 공유.

직렬화 계약 단일 진실: datetime -> ISO 문자열 / dataclass -> dict(asdict). 캐시 스냅샷과 보고서 정적
스냅샷이 동일 규칙을 쓰도록 한 곳에서 정의 (각 serializer 가 byte-identical 헬퍼를 복제하지 않음).
"""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from _typeshed import DataclassInstance


def json_default(obj: object) -> str:
    """json.dumps default — datetime 만 ISO 문자열로. 그 외 타입은 TypeError(직렬화 계약 밖)."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Cannot serialize {type(obj)}")


def to_jsonable(vm: DataclassInstance) -> dict:
    """dataclass ViewModel -> JSONB 저장 가능 dict (datetime -> ISO str, nested 재귀)."""
    return json.loads(json.dumps(dataclasses.asdict(vm), default=json_default))


def parse_dt(v: str | datetime | None) -> datetime | None:
    """ISO 문자열 -> datetime (역직렬화). 이미 datetime/None 이면 그대로."""
    return datetime.fromisoformat(v) if isinstance(v, str) else v
