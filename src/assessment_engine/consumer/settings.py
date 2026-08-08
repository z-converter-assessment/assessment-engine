"""ConsumerSettings의 지연 생성 진입점."""

from functools import cache

from assessment_engine.config import ConsumerSettings


@cache
def get_consumer_settings() -> ConsumerSettings:
    """프로세스 안에서 공유하는 ConsumerSettings 인스턴스를 반환한다."""
    return ConsumerSettings()  # pyright: ignore[reportCallIssue]
