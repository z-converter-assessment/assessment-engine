"""diagnostic 컴포넌트 Settings 인스턴스 단일 진실 (Composition Root, CLAUDE.md #F4).

`diagnostic_settings` (DiagnosticSettings, ConsumerSettings 상속) —
POSTGRES·REDIS·RABBITMQ·LLM_*·DIAGNOSTIC_* 등 진단 워커·스케줄러 운영 키.

본 module은 diagnostic worker·scheduler 노드에서 import.
web 노드도 broker publish용으로 본 클래스 인스턴스를 사용하지만 그 인스턴스는
`web/settings.py`에서 별도 생성 (web 컴포넌트의 lifecycle 묶음).
"""

from assessment_engine.config import DiagnosticSettings

diagnostic_settings = DiagnosticSettings()
