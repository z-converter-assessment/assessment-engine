"""property 테스트 생성 횟수 배율.

각 테스트가 `@settings(max_examples=...)` 로 무게를 선언하는데 hypothesis 는 데코레이터를
프로파일보다 우선하므로, 프로파일 등록으로는 그 값을 못 덮는다. 선언값에 배율을 곱해
테스트 사이의 상대 무게를 유지한 채 전체를 조절한다.
"""

import os

_SCALE = float(os.getenv("HYPOTHESIS_SCALE", "1"))
_FLOOR = 50  # 생성이 몇 번뿐이면 property 테스트가 아니다


def examples(declared: int) -> int:
    """`@settings(max_examples=examples(3000))` 형태로 쓴다."""
    return max(_FLOOR, int(declared * _SCALE))
