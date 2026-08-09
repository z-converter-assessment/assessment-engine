"""Consumer 메시지 핸들러 콜백 계약.

네 핸들러 팩토리와 consumer 조립 코드가 공유한다. 각 구현 모듈이 이 별칭만 import 해 의존 방향을
유지한다.
"""

from collections.abc import Awaitable, Callable

from aio_pika.abc import AbstractIncomingMessage

type MessageHandler = Callable[[AbstractIncomingMessage], Awaitable[None]]
"""`queue.consume`에 등록하는 비동기 메시지 콜백."""
