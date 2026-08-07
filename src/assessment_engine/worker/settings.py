"""worker 컴포넌트 Settings 진입점.

import 이 아니라 첫 호출에서 인스턴스화한다 — 이유는 web/settings.py 와 같다.
"""

from functools import lru_cache

from assessment_engine.config import WorkerSettings


@lru_cache(maxsize=1)
def get_worker_settings() -> WorkerSettings:
    return WorkerSettings()  # pyright: ignore[reportCallIssue]
