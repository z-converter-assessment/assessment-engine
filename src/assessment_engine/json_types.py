"""wire·JSONB 원본 JSON 값의 타입 별칭과 접근 헬퍼.

패키지 루트에 있는 이유 — db·consumer·web 전 계층이 같은 JSON 값 타입을 쓴다. 계층 소속이 없는
어휘라 어느 패키지에 넣어도 나머지가 그 패키지를 import 하게 된다.

에이전트가 보내고 JSONB 컬럼에 그대로 실리는 객체는 키·값이 계약으로만 정해진다. 모델로 좁히면
계약 밖 필드가 도착했을 때 통과시키라는 규약과 어긋나므로 원본은 열린 채로 두고, 읽는 쪽이 필요한
축만 좁혀 쓴다. 계약은 docs/reference/contracts/agent-data.md 가 기준.

값 타입이 열려 있어 `d.get(key) or []` 같은 관용구는 원소 타입을 잃는다. 중첩 배열·객체를 꺼내는
자리는 아래 세 헬퍼를 경유해 그 자리에서 형태를 확정한다.
"""

from typing import Any, cast

type JsonObject = dict[str, Any]


def _member(d: object, key: str) -> object:
    return cast("JsonObject", d).get(key) if isinstance(d, dict) else None


def json_list(d: object, key: str) -> list[JsonObject]:
    value = _member(d, key)
    return cast("list[JsonObject]", value) if isinstance(value, list) else []


def json_str_list(d: object, key: str) -> list[str]:
    value = _member(d, key)
    return cast("list[str]", value) if isinstance(value, list) else []


def json_obj(d: object, key: str) -> JsonObject:
    value = _member(d, key)
    return cast("JsonObject", value) if isinstance(value, dict) else {}
