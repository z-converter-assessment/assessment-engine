"""`to_jsonable` — JSONB·캐시에 저장되는 표현이 `json.dumps` 인코더와 한 글자도 다르지 않아야 한다.

발행된 보고서는 `diagnostic_jobs.result` JSONB 에 정적 스냅샷으로 남고, 그 값은 마이그레이션할 수단이
없다. 표현이 바뀌면 오늘 정상 렌더되는 과거 보고서가 깨진다 — 그래서 "더 나은 표현" 을 고르지 않고
인코더 동작을 그대로 재현한다.

증인은 예전 구현(인코딩 -> 파싱 왕복)이다. 같은 입력에 같은 결과를 내는지 값으로 대조한다.
"""

import dataclasses
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from assessment_engine.web.services.serialization import json_default, to_jsonable


def _round_trip(vm: Any) -> Any:
    """전환 전 구현 — 트리 전체를 JSON 문자열로 인코딩했다 다시 파싱한다."""
    return json.loads(json.dumps(dataclasses.asdict(vm), default=json_default))


@dataclass
class _Leaf:
    when: datetime
    label: str
    ratio: float | None = None


@dataclass
class _Tree:
    """직렬화가 만나는 형태를 한 곳에 모은 표본 — 중첩 dataclass·list·tuple·비문자열 dict 키."""

    root: _Leaf
    children: list[_Leaf]
    pairs: tuple[int, int]
    by_index: dict[int, str]
    by_name: dict[str, Any]
    empty: list[_Leaf] = field(default_factory=list[_Leaf])
    missing: datetime | None = None


def _sample() -> _Tree:
    anchor = datetime(2026, 5, 1, 12, 30, 45, 123456, tzinfo=UTC)
    return _Tree(
        root=_Leaf(when=anchor, label="root", ratio=0.5),
        children=[_Leaf(when=anchor, label="a"), _Leaf(when=anchor, label="b", ratio=1.0)],
        pairs=(1, 2),
        by_index={7: "seven"},
        by_name={"nested": {"deep": [anchor, None, True]}},
    )


def test_matches_the_encoder_round_trip():
    vm = _sample()

    assert to_jsonable(vm) == _round_trip(vm)


def test_serialized_bytes_are_identical():
    """dict 동치로는 키 순서 변화를 못 잡는다 — JSONB 저장값은 문자열이라 순서까지 본다."""
    vm = _sample()

    assert json.dumps(to_jsonable(vm)) == json.dumps(_round_trip(vm))


def test_datetime_keeps_offset_notation():
    """`+00:00` 표기를 유지한다 — `Z` 로 바뀌면 저장된 스냅샷과 새 스냅샷의 표현이 갈린다."""
    result = to_jsonable(_sample())

    assert result["root"]["when"] == "2026-05-01T12:30:45.123456+00:00"


def test_tuple_becomes_list_and_int_keys_become_strings():
    """인코더가 하던 두 변환 — 재현하지 않으면 JSONB 구조가 바뀐다."""
    result = to_jsonable(_sample())

    assert result["pairs"] == [1, 2]
    assert result["by_index"] == {"7": "seven"}
