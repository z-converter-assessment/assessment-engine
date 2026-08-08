"""로그 출력 setup 단일 진실.

패키지 루트에 있는 이유 — web·consumer·worker 세 entry 가 기동 직후 같은 함수를 부른다.
"""

import logging  # noqa: TID251  stdlib logging 을 loguru 로 넘기는 브릿지가 여기 산다
import sys
from typing import Literal, override

from loguru import logger


class _InterceptHandler(logging.Handler):
    """stdlib logging 레코드를 loguru sink 로 넘긴다.

    uvicorn·SQLAlchemy·aio-pika 는 stdlib logging 으로 내보낸다. 브릿지가 없으면 `LOG_FORMAT=json`
    을 켜도 그 로그만 JSON 이 아닌 채 같은 stdout 에 섞여 aggregator 파싱이 깨진다.
    """

    @override
    def emit(self, record: logging.LogRecord) -> None:
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        frame, depth = logging.currentframe(), 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1
        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def setup_logging(log_format: Literal["text", "json"], log_level: str = "INFO") -> None:
    """loguru default sink(stderr)를 제거하고 stdout 으로 재등록 — text 또는 json. stdlib logging 도 같은 sink 로.

    `diagnose=False` 는 타협하지 않는다 — loguru 기본값은 traceback 에 프레임 지역변수를 그대로 찍고,
    그 프레임에는 비밀번호와 메시지 payload 가 들어온다. `backtrace=True` 는 유지한다 — 호출 스택은
    남기되 값은 남기지 않는다.

    중복 호출 안전 — 다시 부르면 sink 만 교체된다.
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
