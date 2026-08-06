"""핸들러 시그니처 별칭 — 4 팩토리와 `consumer/main.py` 가 공유한다.

`handlers/__init__.py` 에 두지 않는다. 그 모듈이 이미 4 핸들러 모듈을 import 하므로 핸들러가 거꾸로
`__init__` 을 import 하면 정확히 순환이다. 별칭 정의를 import 문보다 위에 두면 우연히 통과하지만
그때는 동작이 줄 순서에 매달린다.
"""

from collections.abc import Awaitable, Callable

from aio_pika.abc import AbstractIncomingMessage

type MessageHandler = Callable[[AbstractIncomingMessage], Awaitable[None]]
"""`queue.consume` 에 바인딩하는 콜백. aio-pika 가 반환값을 기다리기만 하므로 Awaitable 로 족하다."""
