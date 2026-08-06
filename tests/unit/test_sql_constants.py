"""SQL 안에 흩어진 상수가 갈라지지 않는지 본다.

원본 문자열을 하나로 모으는 정공은 SQL 을 f-string 으로 바꾸는 것인데, 그러면 SQL 조립에 f-string 을
새로 들이게 된다(#C5 가 경계하는 방향). 그래서 값을 모으는 대신 갈라짐을 실패로 만든다.
"""

import re
from pathlib import Path

_REPO = Path(__file__).parent.parent.parent
_SQL_DIR = _REPO / "src" / "assessment_engine" / "db" / "repositories"

_FUTURE_SKEW = re.compile(r"now\(\)\s*\+\s*interval\s*'([^']+)'")


def test_future_skew_interval_is_single_value():
    """미래 timestamp 방어선은 한 값이어야 한다 — 근거는 `query/_base.py` 상단 주석이 갖는다.

    agent 시계가 어긋나 collected_at 이 미래로 발행되면 그 행이 "가짜 최신" 으로 잡혀 최신 2행 delta 가
    깨진다. 방어선이 파일마다 다르면 같은 사고가 화면마다 다르게 나타난다.
    """
    found: dict[str, list[str]] = {}
    for path in _SQL_DIR.rglob("*.py"):
        for interval in _FUTURE_SKEW.findall(path.read_text(encoding="utf-8")):
            found.setdefault(interval, []).append(path.name)

    assert found, "미래 timestamp 방어 술어를 하나도 못 찾았다 — 패턴이 낡았거나 방어가 사라졌다"
    assert list(found) == ["2 minutes"], f"방어선이 갈라졌다: {found}"
