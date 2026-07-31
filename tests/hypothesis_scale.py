"""property 테스트 생성 횟수 배율 — 한 곳에서 조절한다.

각 테스트가 `@settings(max_examples=...)` 로 자기 무게를 선언하고 있어 프로파일 등록만으로는
덮이지 않는다(데코레이터가 프로파일보다 우선). 그래서 선언값에 배율을 곱하는 방식을 쓴다 —
테스트 사이의 상대 무게는 유지하면서 전체를 한 번에 줄인다.

`HYPOTHESIS_SCALE` 환경변수로 조절한다. 미설정이면 1(선언값 그대로).
"""

import os

_SCALE = float(os.getenv("HYPOTHESIS_SCALE", "1"))

# 배율이 아무리 작아도 이 아래로는 안 내려간다 — 생성이 몇 번뿐이면 property 테스트가 아니다.
_FLOOR = 50


def examples(declared: int) -> int:
    """선언한 생성 횟수에 배율을 적용한다."""
    return max(_FLOOR, int(declared * _SCALE))
