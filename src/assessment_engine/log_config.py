"""로그 출력 setup 단일 진실.

loguru sink 등록 — text(colorized·dev 친화) vs json(외부 log aggregator indexing) 분기.
각 entry(web/consumer/worker)가 기동 직후 호출.

호출 위치는 Composition Root (F4):
- web/main.py module top (FastAPI app 생성 전)
- consumer/main.py main() 시작점
- worker/main.py main() 시작점
"""

import logging  # noqa: TID251  stdlib logging 을 loguru 로 넘기는 브릿지가 여기 산다
import sys
from typing import Literal, override

from loguru import logger


class _InterceptHandler(logging.Handler):
    """stdlib logging 레코드를 loguru sink 로 넘긴다.

    uvicorn·SQLAlchemy·aio-pika 는 stdlib logging 으로 내보낸다. 브릿지가 없으면 `LOG_FORMAT=json`
    을 켜도 그 로그만 JSON 이 아닌 채 같은 stdout 에 섞여, 외부 aggregator 가 파싱에 실패하는 줄이 생긴다.
    """

    @override
    def emit(self, record: logging.LogRecord) -> None:
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        # 호출 지점을 loguru 가 제 위치로 찍도록 stdlib 프레임을 건너뛴다.
        frame, depth = logging.currentframe(), 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1
        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def setup_logging(log_format: Literal["text", "json"], log_level: str = "INFO") -> None:
    """loguru default sink 교체 — text 또는 json. stdlib logging 도 같은 sink 로 모은다.

    text: 콘솔 colorized (default loguru format). dev grep·시연 가독성.
    json: `serialize=True`로 record를 JSON으로 변환. 외부 log aggregator
        (Loki·ELK·CloudWatch·Datadog 등)가 level·time·message·extra 자동 indexing.

    `diagnose=False` 는 타협하지 않는다 — loguru 기본값은 traceback 에 프레임 지역변수를 그대로 찍고,
    그 프레임에는 비밀번호와 메시지 payload 가 들어온다(#F8). `backtrace=True` 는 유지한다: 호출 스택은
    남기되 값은 남기지 않는다.

    호출 시점에 loguru default sink(stderr text) 제거 후 stdout으로 재등록.
    중복 호출 안전 — 다시 호출하면 sink 교체.
    """
    logger.remove()
    logger.add(
        sys.stdout,
        serialize=log_format == "json",
        level=log_level,
        backtrace=True,
        diagnose=False,
    )
    logging.basicConfig(handlers=[_InterceptHandler()], level=0, force=True)
