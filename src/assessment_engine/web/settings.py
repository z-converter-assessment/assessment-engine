"""web 컴포넌트 Settings 인스턴스 단일 진실 (Composition Root, CLAUDE.md #F4).

- `web_settings` (WebSettings): POSTGRES·REDIS·WEB_PORT·INSTALL_* 등 web 라우터·서비스 공통. eager.
- `diagnostic_settings` (DiagnosticSettings): web이 task.install 발행 시 broker·task exchange 사용.
  지연 인스턴스화 (PEP 562 module `__getattr__`) — DB-only 컴포넌트(worker)가 query 계층을 통해
  `web_settings` 만 import 할 때 broker 설정(DiagnosticSettings 의 rabbitmq prod 검증)까지 강제로
  만들지 않게 한다. broker 를 실제 쓰는 web(main·task_service)만 import 시 인스턴스화된다.

multi-node 분리 시 web 노드만 본 module을 import — ConsumerSettings는 만들지 않음.
"""

from assessment_engine.config import DiagnosticSettings, WebSettings

web_settings = WebSettings()

_diagnostic_settings: DiagnosticSettings | None = None


def __getattr__(name: str) -> object:
    if name == "diagnostic_settings":
        global _diagnostic_settings
        if _diagnostic_settings is None:
            _diagnostic_settings = DiagnosticSettings()
        return _diagnostic_settings
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
