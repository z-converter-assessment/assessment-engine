"""`pytest.approx` 의 타입 있는 래퍼.

pytest 는 approx 의 expected·rel·abs 를 타입 없이 선언한다. 그대로 쓰면 비교식 전체가 미상이 되어
검사기가 그 줄의 실제 오류를 더는 보지 못한다. 인자 이름은 pytest 와 같게 둔다.
"""

from typing import Any

import pytest


def approx(expected: float, *, abs: float | None = None, rel: float | None = None) -> Any:  # noqa: A002
    return pytest.approx(expected, abs=abs, rel=rel)
