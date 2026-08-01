"""단위 테스트 공통 픽스처 — Settings 를 실제로 만드는 테스트에 값을 주고, 전역 캐시를 되돌린다.

비밀번호에 기본값이 없어(#F8) `WorkerSettings()` 류를 인자 없이 만드는 테스트는 값을 줄 채널이
있어야 한다. CI 체크아웃에는 `.env` 도 secret 파일도 없고 job env 에도 넣지 않는다 — 여기서 준다.
로컬에서만 통과하고 CI 에서 깨지는 상태를 막는 것이 목적이다.
"""

import pytest

from assessment_engine.consumer.settings import get_consumer_settings
from assessment_engine.db.session import get_engine, get_session_factory
from assessment_engine.web.settings import get_diagnostic_settings, get_web_settings
from assessment_engine.worker.settings import get_worker_settings

_UNIT_TEST_SECRETS = {
    "POSTGRES_PASSWORD": "unit-test-postgres-secret",
    "RABBITMQ_PASSWORD": "unit-test-rabbitmq-secret",
}

_CACHED_ENTRYPOINTS = (
    get_web_settings,
    get_diagnostic_settings,
    get_consumer_settings,
    get_worker_settings,
    get_engine,
    get_session_factory,
)


@pytest.fixture(autouse=True)
def unit_test_secrets(monkeypatch):
    """필수 비밀번호를 env 로 준다. 검증 자체를 다루는 테스트는 delenv 로 걷어낸다."""
    for key, value in _UNIT_TEST_SECRETS.items():
        monkeypatch.setenv(key, value)


@pytest.fixture(autouse=True)
def clear_settings_cache():
    """`lru_cache` Composition Root 는 프로세스 전역이라 테스트 사이에 값이 새 나간다."""
    yield
    for entrypoint in _CACHED_ENTRYPOINTS:
        entrypoint.cache_clear()
