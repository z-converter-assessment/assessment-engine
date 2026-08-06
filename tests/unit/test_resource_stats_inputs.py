"""분류 입력 주입 비대칭 고정 — 어느 호출 경로가 disk baseline 을 넘기는가.

`build_resource_stats(raw, *, disk_baseline)` 는 disk 활동 축을 인자로 받는다. 그 값을 실제로 채우는
것은 보고서 경로(`_assemble_report_raws`) 하나뿐이고 나머지는 None 이다 — 즉 같은 호스트라도 보고서에서만
유휴 판정의 활동 축이 살아 있다.

이 파일은 그 상태가 옳다고 주장하지 않는다. 지금 그렇다는 것을 고정할 뿐이다. 주입을 통일하는 변경은
화면 분류를 실제로 바꾸므로(#E3 화면 간 정합) 이 테스트를 함께 고쳐야 하고, 그 수정이 곧 "의도한
계약 개정" 이라는 표시가 된다.

호출 경로를 소스에서 직접 읽는다 — 목록을 손으로 적으면 새 경로가 늘어도 여기만 옛 상태로 남는다.
"""

import ast
import re
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2] / "src/assessment_engine"

# 경로별 기대 인자. 값은 소스에 쓰인 표현식 그대로다 — "raw 가 실은 것을 쓴다" 와 "None 으로 고정한다" 를
# 구분하는 것이 이 표의 목적이라 정규화하지 않는다.
EXPECTED: dict[str, set[str]] = {
    "web/services/mappers/report.py": {"r.disk_iops_baseline", "raw.disk_iops_baseline"},
    "web/services/query/report.py": {"raw0.disk_iops_baseline"},
    "web/services/query/environment.py": {"raw.disk_iops_baseline"},
    "web/services/query/server.py": {"None"},
    "web/services/mappers/server.py": {"None"},
    "web/services/mappers/attention.py": {"None"},
    "web/services/mappers/assessment_api.py": {"None"},
    "web/services/mappers/right_sizing_api.py": {"None"},
}


def _call_sites() -> dict[str, set[str]]:
    """`build_resource_stats` 호출부의 `disk_baseline` 인자 표현식을 파일별로 모은다."""
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
    """새 호출 경로가 생기면 여기서 먼저 걸린다 — 어느 축을 넘길지 결정하지 않고는 못 지나간다."""
    assert set(_call_sites()) == set(EXPECTED)


@pytest.mark.parametrize("path", sorted(EXPECTED))
def test_call_site_passes_expected_baseline(path: str):
    assert _call_sites()[path] == EXPECTED[path]


def test_only_report_path_fills_the_raw_field():
    """`ReportRowRaw.disk_iops_baseline` 에 값을 공급하는 코드는 보고서 prefetch 한 곳뿐이다.

    이 단정이 위 표의 근거다 — 다른 경로가 이 필드를 채우기 시작하면 "None 으로 고정" 이 사실과 어긋난다.

    raw 가 frozen 이라 대입(`raw.x = v`)이 아니라 `replace` 키워드 dict 로 채워진다. 두 형태를 다 보되
    필드 선언(`disk_iops_baseline: int | None = None`)과 읽기는 제외한다 — 선언까지 세면 DTO·ViewModel·
    도메인 모듈이 전부 걸려 가드가 아무것도 구분하지 못한다.
    """
    supplies = re.compile(r'"disk_iops_baseline"\s*:|\w+\.disk_iops_baseline\s*=[^=]')
    writers = {
        str(path.relative_to(_SRC)) for path in _SRC.rglob("*.py") if supplies.search(path.read_text(encoding="utf-8"))
    }

    assert writers == {"web/services/query/report.py"}
