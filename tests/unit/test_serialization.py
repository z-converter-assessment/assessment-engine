import dataclasses
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from assessment_engine.web.services.serialization import json_default, to_jsonable


def _round_trip(vm: Any) -> Any:
    return json.loads(json.dumps(dataclasses.asdict(vm), default=json_default))


@dataclass
class _Leaf:
    when: datetime
    label: str
    ratio: float | None = None


@dataclass
class _Tree:
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
    vm = _sample()

    assert json.dumps(to_jsonable(vm)) == json.dumps(_round_trip(vm))


def test_datetime_keeps_offset_notation():
    result = to_jsonable(_sample())

    assert result["root"]["when"] == "2026-05-01T12:30:45.123456+00:00"


def test_tuple_becomes_list_and_int_keys_become_strings():
    result = to_jsonable(_sample())

    assert result["pairs"] == [1, 2]
    assert result["by_index"] == {"7": "seven"}
