"""Consumer 메시지 핸들러 sub-package — 4 routing key 별 팩토리.

consumer/main.py 가 본 sub-package 에서 4 factory 를 import 해 aio-pika queue.consume 에 바인딩.
공통 helper (`_db_retry`, `_check_idempotent`, `_log_time_invariants`, `_track_agent_restart`) 는
`handlers/_common.py` 단일 진실 — 4 핸들러가 sibling import.
"""

from assessment_engine.consumer.handlers.error import make_error_handler
from assessment_engine.consumer.handlers.inventory import make_inventory_handler
from assessment_engine.consumer.handlers.metrics import make_metrics_handler
from assessment_engine.consumer.handlers.task_result import make_task_result_handler

__all__ = [
    "make_error_handler",
    "make_inventory_handler",
    "make_metrics_handler",
    "make_task_result_handler",
]
