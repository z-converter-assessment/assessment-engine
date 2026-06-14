"""consumer 컴포넌트 Settings 인스턴스 단일 진실 (Composition Root, CLAUDE.md #F4).

`consumer_settings` (ConsumerSettings, WebSettings 상속) — POSTGRES·REDIS·RABBITMQ·WORKER_* 등 메시지 컨슈머 운영 키.

multi-node 분리 시 consumer 노드만 본 module을 import — DiagnosticSettings(web 전용 task.install 발행)는 만들지 않음.
"""

from assessment_engine.config import ConsumerSettings

consumer_settings = ConsumerSettings()
