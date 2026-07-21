# 컴포넌트 정석성 감사 — 리포트

상태: 진행 중 (Domain 1-2 완료)
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

---

## Domain 2: 저장 계층

### U5 DB 모델·repository·DTO — canonical

- 현재: SQLAlchemy 2.0(Mapped/mapped_column). 시계열 자연키 UNIQUE(uq_server_metrics_sid_ts 등) + composite PK(id, collected_at — hypertable 파티션 컬럼 PK 포함 요건). pg_insert on_conflict_do_nothing/do_update(canonical upsert). upsert_server = 앱레벨 변경감지 + history append. ensure_server_id = INSERT ON CONFLICT DO NOTHING + re-find race 처리. 자식 bulk INSERT(vars(e) shallow spread로 asdict deepcopy 회피). BaseCollectRepository ABC. 트랜잭션 경계는 호출자(_db_retry).
- 근거: hypertable PK 요건·Postgres upsert·인터페이스 우선 DI·race-safe upsert 다 정석. record_metrics 7연속 await은 단일 asyncpg 커넥션에서 동시쿼리 불가라 순차가 옳음(gather 불가) — 이탈 아님.

### U6 시계열·cagg·counter_agg·통계쿼리 — canonical (핵심) + improvable 후보 3건

- 현재(정석): counter reset-safe 집계를 timescaledb_toolkit counter_agg(delta/time_delta)로 단일화 — 재부팅/재시작 gate 불요. 5분 cagg 5종 + query 시 percentile_cont/regr_slope. WHERE bucket 하한·상한 pruning(C5). LEFT JOIN inventory로 metric 없는 서버도 반환(N+1 회피). 물리/가상 필터를 cagg에 안 박고 query 조인으로 선별(#C4 재생성 고통 해소). 시계열 집계 아키텍처 자체는 교과서.
- improvable 후보(named cost — 사용자 지목 "통계 쿼리 최적화"와 정확히 일치):
  - B1 (위험 낮음 / 확신 높음): report_aggregate의 disk_await CTE와 disk_io_base CTE가 server_disk_io_5m을 동일 WHERE+물리필터로 각각 스캔. 단일 per-bucket CTE로 병합 가능(결과 동일). named cost: cagg 2중 스캔 + 물리필터 2중 평가. SQL-only 리팩터.
  - B2 (EXPLAIN 선행): 물리 device/iface 필터가 상관 EXISTS + CROSS JOIN LATERAL jsonb_array_elements라 cagg/hypertable 행마다 JSONB 배열 전개·문자열 매칭. 물리 device_id/iface_id 집합은 서버당 사실상 정적 -> 서버당 1회 전개하는 사전계산 CTE로 hash-join 가능. named cost: O(buckets*devices) JSONB 전개 -> O(servers). 단 현대 Postgres 플래너가 semi-join hoist할 여지 있어 EXPLAIN ANALYZE 실측 후 확정. (types.py 주석이 "raw+cagg 양쪽 재사용 위해 상관 서브쿼리" 명시 — 재사용성 위해 성능 희생한 의식적 선택이나 사전계산 CTE도 재사용 가능.)
  - B3 (cagg schema change, 규모 중): environment_utilization·report_memory_breakdown_batch가 raw server_metrics/server_filesystem 스캔. 이유는 capacity-weighting(sum(used)/sum(total))이 raw byte 필요한데 cagg는 %만 materialize(mem_pct_avg), byte gauge·cached/buffered% 부재 — 즉 현재 raw 스캔은 정당. cagg에 byte gauge + cached/buffered% 추가하면 raw -> cagg 전환 가능. named cost: 최대 30일 raw 스캔 vs cagg(약 8640버킷). 단 마이그레이션 + cagg 재생성(#C4) 동반이라 규모 큼.
- 판정: 아키텍처 canonical, 위 3건은 실제 비용 있는 improvable. B1 즉시 가치, B2 실측 선행, B3 규모 있음.

### U7 Redis fail-open — canonical

- 현재: ConnectionPool(socket_timeout 5·connect_timeout 3·health_check_interval 30·keepalive·max_connections 50) + safe_* 래퍼(RedisError catch -> fail-open). safe_incr_with_ttl은 INCR+EXPIRE를 MULTI/EXEC 파이프라인 원자.
- 근거: 장수 async 풀 정석(health_check_interval로 idle-cut 죽은 소켓 방어 = 현행 권고) + fail-open 경계(timeout) + bounded pool. safe_set_nx None 반환으로 호출자 fail-open 결정 위임 — 멱등성 2단(#D2)과 정합. 이탈 없음.

### Domain 2 종합

- canonical: U5, U7, U6(집계 아키텍처)
- improvable: U6 3건(B1 즉시·B2 실측선행·B3 규모)
- 핵심 학습: 사용자 직관대로 "통계 쿼리"에 실제 최적화 여지 존재 — 단 시계열 집계 설계 자체(counter_agg·cagg·pruning)는 정석. 최적화는 "재작성"이 아니라 타깃 SQL 리팩터(중복 스캔 제거·상관 서브쿼리 사전계산)로 국한.

---

## 개선 backlog (누적)

| ID | 도메인 | 항목 | 위험 | 확신 | named cost |
|----|--------|------|------|------|-----------|
| B1 | D2/U6 | report_aggregate disk cagg 이중 스캔 병합 | 낮음 | 높음 | cagg 2중 스캔 + 필터 2중 평가 |
| B2 | D2/U6 | 물리 device/iface 상관 EXISTS -> 사전계산 CTE | 중 | 중(EXPLAIN 선행) | O(buckets*devices) JSONB 전개 |
| B3 | D2/U6 | env util/mem breakdown raw -> cagg 확장 | 중 | 중 | 최대 30d raw 스캔 vs cagg (마이그 동반) |
| (유예) | D1/U3 | HA 전환 시 task.result 큐 quorum | - | - | 단일 노드 현행 0 |
