from typing import Any, get_args

import pytest

from assessment_engine.domain import right_sizing
from assessment_engine.web.services.mappers.constants import _DONUT_SEGMENT_DEFS, BADGE_CLASS

EXHAUSTIVE_MAPS: list[tuple[str, dict[Any, Any], Any]] = [
    ("RECOMMENDATION_LABEL_KO", right_sizing.RECOMMENDATION_LABEL_KO, right_sizing.Recommendation),
    ("CLASSIFICATION_ORDER", right_sizing.CLASSIFICATION_ORDER, right_sizing.Recommendation),
    ("RECOMMENDATION_ACTION_KO", right_sizing.RECOMMENDATION_ACTION_KO, right_sizing.Recommendation),
    ("BADGE_CLASS", BADGE_CLASS, right_sizing.Recommendation),
    ("TRIGGER_LABEL_KO", right_sizing.TRIGGER_LABEL_KO, right_sizing.TriggerKind),
    ("RESOURCE_KIND_LABEL_KO", right_sizing.RESOURCE_KIND_LABEL_KO, right_sizing.ResourceKind),
    ("RESOURCE_STATUS_LABEL_KO", right_sizing.RESOURCE_STATUS_LABEL_KO, right_sizing.ResourceStatus),
]


@pytest.mark.parametrize(("name", "mapping", "alias"), EXHAUSTIVE_MAPS, ids=[m[0] for m in EXHAUSTIVE_MAPS])
def test_mapping_covers_literal_catalog(name: str, mapping: dict[Any, Any], alias: Any):
    assert set(mapping) == set(get_args(alias.__value__)), name


def test_resource_action_base_has_actionable_resources_only():
    assert set(right_sizing._RESOURCE_ACTION_BASE) == {"cpu", "memory", "disk_capacity", "disk_io"}


def test_sizing_target_label_has_numeric_target_resources_only():
    assert set(right_sizing._SIZING_TARGET_LABEL) == {"cpu", "memory", "disk_capacity"}


def test_trigger_literals_match_what_the_domain_emits():
    import ast
    from pathlib import Path

    source = (Path(right_sizing.__file__)).read_text(encoding="utf-8")
    emitted: set[str] = set()
    for node in ast.walk(ast.parse(source)):
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
        if isinstance(node, ast.keyword) and node.arg == "triggers" and isinstance(node.value, ast.List):
            emitted.update(str(e.value) for e in node.value.elts if isinstance(e, ast.Constant))

    assert emitted == set(get_args(right_sizing.TriggerKind.__value__))


def test_donut_segment_order_is_the_dropdown_order():
    from assessment_engine.web.services.mappers.constants import PROVISIONING_CLASS_OPTIONS

    assert [key for key, _, _ in _DONUT_SEGMENT_DEFS] == [key for key, _ in PROVISIONING_CLASS_OPTIONS]
