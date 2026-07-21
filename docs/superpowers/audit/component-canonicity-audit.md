# 컴포넌트 정석성 감사 — 리포트

상태: 진행 중 (Domain 1 완료)
날짜: 2026-07-21
설계 spec: ../specs/2026-07-21-component-canonicity-audit-design.md
성격: 내부 방법론 작업 자료 (외부 공유용 아님 — repo 내부 코드·규약 인용을 포함).

판정 루브릭
- canonical: 현행 best practice와 일치. 무변경.
- acceptable: 교과서 정석은 아니나 도메인·제약이 정당화. 유지 + 이유.
- improvable: 정석 이탈이 실제 비용을 유발. 개선 후보 — named cost(성능/정합성/유지보수) + 규모 필수.

정석 참조 출처: context7로 당긴 현행 라이브러리 공식 docs + 확립된 아키텍처 패턴.

---

## Domain 1: 수집 파이프라인

### U1 메시지 계약·wire 버저닝 — canonical

- 현재: Pydantic v2(extra=ignore, populate_by_name), schema_version Literal["1.0"], message_type별 서브모델(Metrics/Inventory/Error/TaskResult Input), CONTRACT_VERSION 단일진실(contract.py, major.minor, major-gate), field_validator 정규화(composite_id ""->None, IP 형식).
- 정석 참조: Pydantic v2 forward-compat = extra=ignore + model_validate_json. 진입점 1회 검증. wire 버저닝 = major-gate + additive-minor.
- 근거: 계약 진화·부분배포 내성·단일 검증 경로 다 정석. 판별을 payload discriminated union이 아니라 routing-key로 하는 것도 AMQP 정석(라우팅키 = 메시지 타입). 이탈 없음.

### U2 멱등성 2단 방어 — acceptable

- 현재: 1단 Redis SET NX(fail-open, None->True) + 2단 DB UNIQUE + pg_insert on_conflict_do_nothing. at-most-once.
- 정석 참조: 메시지 exactly-once에 가장 근접한 정석은 transactional inbox(멱등키를 write와 동일 DB 트랜잭션에 persist). 현재는 그보다 약함 — T1 문서화(SET NX가 commit 전 -> 사이 crash 시 재전송 silent drop).
- 근거: 도메인이 metrics(고volume·손실 허용) -> at-most-once 합리적. DB UNIQUE가 Redis 장애 흡수. 문서화된 의식적 tradeoff(T1)라 미학 문제 아님. inbox는 도메인 가치 대비 과함 -> 유지.
- nuance: DLQ된 메시지도 멱등키가 SET됨 -> 24h 내 DLQ 수동 재처리가 멱등 드롭 가능. _db_retry가 transient를 먼저 흡수(DLQ=영구실패)라 실무 영향 작음.

### U3 MQ 토폴로지 — acceptable

- 현재: durable direct exchange x2(collect/task) + exchange별 DLX + queue별 DLQ(.dead) + x-dead-letter-* + x-message-ttl + x-max-length(overflow->DLX) + prefetch 10 + connect_robust(timeout 10). 큐는 classic durable(x-queue-type 미지정).
- 정석 참조(RabbitMQ docs /quorum-queues·/vhosts): quorum queue가 replicated 큐의 기본 선택이나, 용도 가이드가 명확 — quorum은 "유실이 correctness에 치명적인 long-lived 중요 큐(주문·투표)"용, "stock ticker·instant messaging" 류엔 부적합. classic mirrored 큐는 폐기(4.x 제거) -> HA는 quorum이 유일 경로.
- 근거: 시스템 큐에 대입 -> metrics 큐 = 고volume ticker류라 quorum 부적합, classic이 적절. error 큐 = 5분 TTL ephemeral, classic 적절. inventory·task.result = 저volume이나 단일 노드 브로커(단일 호스트 compose)에선 classic durable 무방. "전부 quorum" 식 순진한 모던화는 metrics에선 오히려 틀림 -> 현행 all-classic은 단일 노드 전제에서 방어 가능.
- 조건부 backlog(active improvable 아님, 유예): 브로커 HA 전환 시 task.result(가능하면 inventory) 큐는 quorum이 정석 — 데이터 안전·at-least-once dead-lettering·delivery-limit poison 처리. 단일 노드 현행에선 비용 0이라 유예.

### U4 DLQ·retry·실패 모델 — canonical

- 현재: message.process(requeue=False) -> 예외 시 nack(no requeue)->DLX->DLQ / 정상 시 ack. ValidationError->raise->DLQ(poison 격리). _db_retry는 Operational/InterfaceError만 3회 exp backoff+full jitter, IntegrityError 등 즉시 raise->DLQ. task_id 미존재·중복->silent ack. DB fail-close / Redis fail-open.
- 정석 참조(aio-pika docs): 정석 소비 패턴 = connect_robust + channel.set_qos(prefetch) + `async with message.process(): ...` + queue.consume. process() 기본은 예외 시 requeue -> 코드의 requeue=False는 poison storm 방지 의도적 override(DLX 존재 + app재시도 전제). connect_robust = 투명 재연결.
- 근거: aio-pika 정석과 정확히 일치. requeue=False는 naive 기본보다 나은 의도적 선택. transient/permanent 분류를 app에서 정밀 처리(broker 맹목 redelivery보다 우수). full jitter backoff. 광역 except 없음. 이탈 없음.

### Domain 1 종합

- canonical: U1, U4
- acceptable: U2(약한 at-most-once, 의식적 tradeoff), U3(단일 노드 전제 classic)
- active improvable: 없음
- 유예 backlog: HA 전환 시 task.result 큐 quorum 검토 (단일 노드 현행 비용 0)

핵심 학습: "모던 기본값(quorum)"이 무조건 정답이 아니다 — RabbitMQ 자체 가이드가 ticker류(metrics)엔 quorum 부적합이라 명시. 현행 설계는 큐 타입을 도메인에 맞게 (암묵적으로) 옳게 골랐다.
