"""web 컴포넌트 Settings 진입점.

import 이 아니라 첫 호출에서 인스턴스화한다. 모듈 로드만으로 설정을 읽으면 비밀번호를 필수 필드로
둘 수 없고(테스트가 import 만 해도 값을 요구한다), 검증이 언제 도는지도 흐려진다.

- `get_web_settings()` — POSTGRES·REDIS·WEB_PORT·INSTALL_* 등 web 라우터·서비스 공통.
- `get_diagnostic_settings()` — web 이 task.install 을 발행할 때 쓰는 broker·task exchange 설정.
  분리해 둔 이유는 DB 만 쓰는 경로가 broker 설정 검증까지 통과하지 않아도 되게 하려는 것이다.

multi-node 분리 시 web 노드만 본 module 을 쓴다 — ConsumerSettings 는 만들지 않는다.
"""

from functools import lru_cache

from assessment_engine.config import DiagnosticSettings, WebSettings


@lru_cache(maxsize=1)
def get_web_settings() -> WebSettings:
    return WebSettings()  # pyright: ignore[reportCallIssue]


@lru_cache(maxsize=1)
def get_diagnostic_settings() -> DiagnosticSettings:
    return DiagnosticSettings()  # pyright: ignore[reportCallIssue]
