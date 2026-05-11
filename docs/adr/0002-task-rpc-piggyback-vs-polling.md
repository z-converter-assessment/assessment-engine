# ADR 0002 — Task 명령 전달 모델: RPC piggyback 채택

상태: 채택 (2026-05-10)

## Context

운영자가 등록 서버에 원격 작업(`zconverter_install` 등)을 발행 → agent가 받아 실행하는 흐름이 필요. 제약:

- 폐쇄망: agent → engine outbound만 정상 (방화벽 inbound 차단 가정)
- 기존 인프라: agent가 RabbitMQ producer (`server.metrics` 발행 중) + HTTP client 추가 가능
- 운영자 latency 기대: "Install 클릭 후 1분 이내 실행 시작" 정도면 충분 (실시간 푸시 요구 없음)

## Options

### A. HTTP polling (별도 endpoint)
agent가 metrics 발행과 같은 주기로 `GET /api/tasks/{hostname}` 호출. engine이 Redis pending 키 조회 후 응답.

- 장점: HTTP 단순, 양방향 큐 토폴로지 추가 없음
- 단점: 별도 polling RTT 추가 (metrics 1회 + task 1회 = 2 RTT/주기). N대 X 60s = 분당 N회 추가 요청

### B. RPC piggyback (`amq.rabbitmq.reply-to`)
agent가 `server.metrics` 발행 시 `reply_to=amq.rabbitmq.reply-to` + `correlation_id` 명시. consumer가 metrics 처리 후 Redis EXISTS → 있으면 reply publish.

- 장점: polling endpoint·queue 신설 0. 기존 metrics 흐름에 piggyback (RTT 통합). 인프라 재활용
- 단점: latency = metrics 주기 (즉시성 X). consumer가 task 조회 책임 추가

### C. MQ push (별도 queue, per-hostname)
engine이 `tasks` exchange + `tasks.{hostname}` durable queue에 명령 publish. agent가 자기 queue subscribe.

- 장점: 즉시 push (~ms latency). broker가 message persistence + retry 자동 처리
- 단점: per-hostname queue 관리 (orphan 누적 위험 — `x-expires` 필요). agent 코드 변경 폭 큼 (consumer 추가)

### D. SSE (Server-Sent Events)
agent가 long-running GET → engine이 task 생길 때 한 줄 push.

- 장점: HTTP 단방향 (폐쇄망 호환), latency ~0
- 단점: long-running connection 관리 (keepalive·재연결). C에서 SSE 파싱 직접 구현. 운영 잡일

## Decision

옵션 B (RPC piggyback) 채택.

근거:
1. Latency 60s 허용 가정 — 운영자 "Install 클릭 후 1분 이내" 충분
2. 인프라 재활용 — 새 queue·endpoint 신설 0 (기존 `server.metrics` channel 위에 piggyback)
3. agent 코드 변경 최소 — `reply_to` props 추가 + 같은 channel에서 reply consume (correlation_id 매칭)
4. `amq.rabbitmq.reply-to` 빌트인 pseudo-queue — 큐 declare·정리 불필요, broker 부하 0
5. C(MQ push)는 운영 부담 큼 (per-hostname queue + orphan 정리 정책). 즉시성이 진짜 필요해질 때 전환

## Consequences

### 긍정
- Polling endpoint 0개. tasks queue 0개. 새 routing key는 결과 보고용 `task.result` 1개만
- agent 변경: `reply_to`/`correlation_id` props + consume 1개 + `task_type` dispatcher
- engine 변경: metrics handler 후처리에 `_reply_pending_task_if_any` 1개 + Redis `task:pending:{machine_id}` 키
- DB·Redis 책임 분리 명확: DB는 audit log (영구), Redis는 hot path (24h TTL)

### 부정·한계
- Latency = metrics 주기 — 즉시성 필요 시 옵션 C로 전환 필요 (별도 작업)
- consumer가 task 조회 책임 추가 — 매 metrics 메시지마다 Redis EXISTS 1회. 99% no-op이라 hot path 영향 미미
- agent의 reply_to 미지원 시 task 영구 미실행 — 단 옛 agent 호환 (reply_to 없으면 consumer가 reply 생략 → 다음 주기 재시도 가능)
- task_type enum 동기화 필수 — engine·agent 양쪽 합의. 새 type 도입 시 `agent_version` bump

### 즉시성 요구 발생 시 전환 경로
옵션 C로 전환:
1. `tasks` exchange + `tasks.{hostname}` queue (`x-expires=7d`, `x-message-ttl=24h`) 신설
2. agent에 consumer 추가
3. engine은 RPC piggyback 코드 제거 + tasks queue publish로 변경
4. Redis `task:pending` 키 역할 사라짐 (broker가 message 보유)

본 ADR의 Redis pending·RPC piggyback 흐름은 그 시점에 deprecated.

## 관련 문서
- 흐름·메시지 스키마·task_type enum: `docs/architecture/agent.md` "Task RPC piggyback"
- Redis 키 정책: `docs/architecture/redis.md` `task:pending` 항목
- 부가 시그널 + consumer 흐름: `docs/architecture/consumer.md` "metrics 후처리" + "부가 시그널"
- CLAUDE.md B6: 결정 요약

## 정정 이력

- 2026-05-11: `task_type` dispatcher와 함께 `params` 스키마도 engine-agent 양쪽 합의 + `agent_version` bump 대상이라는 점 명시. 현재 `zconverter_install`의 params 키는 `source_host`(단일 진실은 코드·agent.md). 본 ADR은 명명을 박지 않음 — 명명 변경은 코드 변경으로 처리.
