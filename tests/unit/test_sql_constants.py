import re
from pathlib import Path

_REPO = Path(__file__).parent.parent.parent
_SQL_DIR = _REPO / "src" / "assessment_engine" / "db" / "repositories"

_FUTURE_SKEW = re.compile(r"now\(\)\s*\+\s*interval\s*'([^']+)'")


def test_future_skew_interval_is_single_value():
    found: dict[str, list[str]] = {}
    for path in _SQL_DIR.rglob("*.py"):
        for interval in _FUTURE_SKEW.findall(path.read_text(encoding="utf-8")):
            found.setdefault(interval, []).append(path.name)

    assert found, "미래 timestamp 방어 술어를 하나도 못 찾았다 — 패턴이 낡았거나 방어가 사라졌다"
    assert list(found) == ["2 minutes"], f"방어선이 갈라졌다: {found}"
