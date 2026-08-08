import ast
import re
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2] / "src/assessment_engine"

EXPECTED: dict[str, set[str]] = {
    "web/services/mappers/report.py": {"raw.disk_iops_baseline"},
    "web/services/mappers/report_summary.py": {"r.disk_iops_baseline"},
    "web/services/query/report.py": {"raw0.disk_iops_baseline"},
    "web/services/query/environment.py": {"raw.disk_iops_baseline"},
    "web/services/query/server.py": {"None"},
    "web/services/mappers/server.py": {"None"},
    "web/services/mappers/attention.py": {"None"},
    "web/services/mappers/assessment_api.py": {"None"},
    "web/services/mappers/right_sizing_api.py": {"None"},
}


def _call_sites() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for path in sorted(_SRC.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if "build_resource_stats(" not in source:
            continue
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.Call):
                continue
            if not (isinstance(node.func, ast.Name) and node.func.id == "build_resource_stats"):
                continue
            for kw in node.keywords:
                if kw.arg == "disk_baseline":
                    key = str(path.relative_to(_SRC))
                    found.setdefault(key, set()).add(ast.unparse(kw.value))
    return found


def test_every_call_site_is_accounted_for():
    assert set(_call_sites()) == set(EXPECTED)


@pytest.mark.parametrize("path", sorted(EXPECTED))
def test_call_site_passes_expected_baseline(path: str):
    assert _call_sites()[path] == EXPECTED[path]


def test_only_report_path_fills_the_raw_field():
    supplies = re.compile(r'"disk_iops_baseline"\s*:|\w+\.disk_iops_baseline\s*=[^=]')
    writers = {
        str(path.relative_to(_SRC)) for path in _SRC.rglob("*.py") if supplies.search(path.read_text(encoding="utf-8"))
    }

    assert writers == {"web/services/query/report.py"}
