"""보고서 발행 job RabbitMQ publish — web `emit_report` 가 호출 (#F4 의존 경계).

ADR 0014 분리 본질 유지 — `web.services` 의존 없이 diagnostic 패키지 안에 둔다 (worker 와 공유 가능).
ADR 0023: scheduler 폐기. AI 진단 독립 발행(submit)·이력은 engineer 보고서 발행 통합으로 폐기 —
본 모듈은 발행 job publish + input_hash/anchor helper 만 유지한다.
"""

import hashlib
import json
from datetime import UTC, datetime

import aio_pika
from aio_pika.abc import AbstractChannel

from assessment_engine.diagnostic.settings import diagnostic_settings


class DiagnosticSubmitter:
    """보고서 발행 job publish 단일 책임 — web `DiagnosticService.emit_report` 가 사용.

    engineer 보고서 발행 시 worker 가 narrative 를 채우도록 {"job_id": id} 메시지를 publish.
    persistent delivery (broker 재시작 생존). composition root 가 broker_channel 주입.
    """

    def __init__(self, broker_channel: AbstractChannel):
        self.broker_channel = broker_channel

    async def publish_job(self, job_id: str) -> None:
        """RabbitMQ publish — 진단 워커가 소비. persistent delivery (broker 재시작 생존).

        engineer 보고서 발행(emit_report)이 호출 — 워커는 {"job_id": id} 만 받아 job_type 으로 분기.
        """
        message = aio_pika.Message(
            body=json.dumps({"job_id": job_id}).encode(),
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            content_type="application/json",
        )
        exchange = await self.broker_channel.get_exchange(diagnostic_settings.rabbitmq_exchange)
        await exchange.publish(
            message,
            routing_key=diagnostic_settings.diagnostic_routing_key,
        )


def _compute_hash(scope: str, input_params: dict) -> str:
    canonical = json.dumps(input_params, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{scope}|{canonical}".encode()).hexdigest()


def _normalize_anchor(at: datetime | None) -> datetime:
    """anchor 분 단위 truncate — 같은 분 발행은 같은 input_hash (더블클릭 dedup).

    None이면 now() UTC 분 단위. 명시 시 timezone-aware 후 UTC 변환 + 분 단위.
    """
    if at is None:
        at = datetime.now(UTC)
    elif at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    return at.astimezone(UTC).replace(second=0, microsecond=0)
