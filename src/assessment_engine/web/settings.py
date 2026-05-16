"""web 컴포넌트 Settings 인스턴스 단일 진실 (Composition Root, CLAUDE.md #F4).

web 노드는 두 Settings 모두 필요:
- `web_settings` (WebSettings): POSTGRES·REDIS·WEB_PORT·INSTALL_* 등 web 라우터·서비스 공통
- `diagnostic_settings` (DiagnosticSettings): web이 진단 publish (POST /api/v1/diagnostics) 시 broker·routing key 사용

multi-node 분리 시 web 노드만 본 module을 import — ConsumerSettings는 만들지 않음.
"""
from assessment_engine.config import DiagnosticSettings, WebSettings

web_settings = WebSettings()
diagnostic_settings = DiagnosticSettings()
