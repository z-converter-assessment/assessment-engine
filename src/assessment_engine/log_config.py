"""로그 출력 setup 단일 진실.

loguru sink 등록 — text(colorized·dev 친화) vs json(외부 log aggregator indexing) 분기.
각 entry(web/consumer/worker)가 기동 직후 호출.

호출 위치는 Composition Root (F4):
- web/main.py module top (FastAPI app 생성 전)
- consumer/main.py main() 시작점
- worker/main.py main() 시작점
"""

import sys
from typing import Literal

from loguru import logger


def setup_logging(log_format: Literal["text", "json"]) -> None:
    """loguru default sink 교체 — text 또는 json.

    text: 콘솔 colorized (default loguru format). dev grep·시연 가독성.
    json: `serialize=True`로 record를 JSON으로 변환. 외부 log aggregator
        (Loki·ELK·CloudWatch·Datadog 등)가 level·time·message·extra 자동 indexing.

    호출 시점에 loguru default sink(stderr text) 제거 후 stdout으로 재등록.
    중복 호출 안전 — 다시 호출하면 sink 교체.
    """
    logger.remove()
    if log_format == "json":
        logger.add(sys.stdout, serialize=True)
    else:
        logger.add(sys.stdout)
