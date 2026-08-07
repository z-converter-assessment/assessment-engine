"""분류 어휘 dict 가 Literal 카탈로그를 빠짐없이 덮는지 — 폴백 없는 인덱싱의 근거.

라벨 조회에서 `.get(key, default)` 폴백을 걷고 `[key]` 직접 인덱싱으로 바꿨다. 그 근거가 "키가 전부
있다" 이므로, 근거 자체를 여기서 고정한다. 카탈로그에 값이 하나 늘고 dict 가 안 따라오면 화면이
KeyError 500 을 내는데, 그 순간을 배포가 아니라 여기서 만난다.

기대 키 목록을 손으로 적지 않는다 — `get_args` 로 Literal 에서 직접 뽑는다. 손으로 적으면 카탈로그와
테스트가 같이 낡는다. PEP 695 별칭은 `__value__` 를 거쳐야 `get_args` 가 값을 준다(그냥 넘기면
빈 튜플이 나와 비교가 조용히 통과한다).
"""

from typing import Any, get_args

import pytest

from assessment_engine.domain import right_sizing
from assessment_engine.web.services.mappers.constants import _DONUT_SEGMENT_DEFS, BADGE_CLASS

# (이름, 매핑, Literal 별칭) — 폴백 없이 인덱싱하는 dict 전부.
EXHAUSTIVE_MAPS: list[tuple[str, dict[Any, Any], Any]] = [
    ("_HOST_STATUS_TO_REC", right_sizing._HOST_STATUS_TO_REC, right_sizing.HostStatus),
    ("RECOMMENDATION_LABEL_KO", right_sizing.RECOMMENDATION_LABEL_KO, right_sizing.Recommendation),
    ("CLASSIFICATION_ORDER", right_sizing.CLASSIFICATION_ORDER, right_sizing.Recommendation),
    ("RECOMMENDATION_ACTION_KO", right_sizing.RECOMMENDATION_ACTION_KO, right_sizing.Recommendation),
    ("BADGE_CLASS", BADGE_CLASS, right_sizing.Recommendation),
    ("TRIGGER_LABEL_KO", right_sizing.TRIGGER_LABEL_KO, right_sizing.TriggerKind),
    ("RESOURCE_KIND_LABEL_KO", right_sizing.RESOURCE_KIND_LABEL_KO, right_sizing.ResourceKind),
    ("_UNDER_ACTION_BASE", right_sizing._UNDER_ACTION_BASE, right_sizing.ResourceKind),
    ("STATUS_LABEL_KO", right_sizing.STATUS_LABEL_KO, right_sizing.ResourceStatus),
    ("HOST_STATUS_LABEL_KO", right_sizing.HOST_STATUS_LABEL_KO, right_sizing.HostStatus),
]


@pytest.mark.parametrize(("name", "mapping", "alias"), EXHAUSTIVE_MAPS, ids=[m[0] for m in EXHAUSTIVE_MAPS])
def test_mapping_covers_literal_catalog(name: str, mapping: dict[Any, Any], alias: Any):
    assert set(mapping) == set(get_args(alias.__value__)), name


def test_trigger_literals_match_what_the_domain_emits():
    """`TriggerKind` 가 실제로 발행되는 trigger 문자열 집합과 같다.

    라벨 dict 와 카탈로그가 서로를 검증하면 동어반복이다 — 발행 지점(소스의 문자열 리터럴)을 증인으로 쓴다.
    """
    import ast
    from pathlib import Path

    source = (Path(right_sizing.__file__)).read_text(encoding="utf-8")
    emitted: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        # triggers.append("...")
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "append"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "triggers"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            emitted.add(str(node.args[0].value))
        # triggers=["..."]
        if isinstance(node, ast.keyword) and node.arg == "triggers" and isinstance(node.value, ast.List):
            emitted.update(str(e.value) for e in node.value.elts if isinstance(e, ast.Constant))

    assert emitted == set(get_args(right_sizing.TriggerKind.__value__))


def test_donut_segment_order_is_the_dropdown_order():
    """세그먼트 순서가 곧 서버 목록 드롭다운 option 순서다 — 정렬을 끼워 넣으면 DOM 이 바뀐다."""
    from assessment_engine.web.services.mappers.constants import PROVISIONING_CLASS_OPTIONS

    assert [key for key, _, _ in _DONUT_SEGMENT_DEFS] == [key for key, _ in PROVISIONING_CLASS_OPTIONS]
