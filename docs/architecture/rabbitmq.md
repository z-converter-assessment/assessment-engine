# RabbitMQ

정책: CLAUDE.md #B. 본 문서는 broker 토폴로지·큐 정책·dev/prod 분기 단일 진실. 코드 동작은 `docs/architecture/consumer.md`.

---

## 1. 본 시스템 결정

vhost: `/assessment` 단일 사용. broker 한 대를 다른 도메인 시스템과 나눠 쓸 때만 추가 vhost 도입. AMQP URL은 `amqp://user:pass@host:port/%2Fassessment` 형식 — `/`는 `%2F`로 인코딩 (config.py 자동 처리).

권한 모델: RabbitMQ는 `(user, vhost)` 쌍에 configure/write/read 3비트를 정규식 패턴으로 부여. dev는 단일 user `assessment`가 셋 모두 보유. prod는 4-user least-privilege로 분리 (#3 dev/prod 분기).

도구 일반론(vhost·권한 비트 의미)은 RabbitMQ 공식 문서.

---

## 2. 본 시스템 토폴로지

### 정의

| 항목 | 값 |
|------|-----|
| Vhost | `/assessment` |
| Exchange | `assessment` (direct, durable) |
| DLX | `assessment.dlx` (direct, durable) |
| prefetch_count | 10 |
| 메시지 delivery_mode | `persistent` (2) — 에이전트 publish 측 설정 |

### 큐 정책

| routing key | 발행 주체 | 큐 | DLQ | TTL | x-max-length |
|-------------|-----------|-----|-----|-----|--------------|
| `server.inventory` | agent | `server.inventory` | `server.inventory.dead` | 없음 (1시간 주기 자동 재발행으로 보강) | 없음 |
| `server.metrics` | agent | `server.metrics` | `server.metrics.dead` | 72h | 1,000,000 |
| `server.error` | agent | `server.error` | `server.error.dead` | 300s | 없음 |
| `task.result` | agent | `task.result` | `task.result.dead` | 24h | 100,000 |
| `diagnostic.request` | engine 내부 (web·스케줄러 → 워커, ADR 0004) | `diagnostic.request` | `diagnostic.request.dead` | 24h | 100,000 |

`server.metrics` 정책 근거:
- 72h TTL: 1분 주기 발행 + consumer/DB 단기 장애(최대 3일) 내 회복 시 누적 메시지 정상 처리.
- 1M 메시지 상한: 약 3KB X 1M = 약 3GB 디스크/메모리 -> broker 폭주 방어.
- 초과 시 oldest 메시지부터 DLX(`server.metrics.dead`)로 routing.

`server.error` 300s TTL: 알림용 노이즈 방지. DB 저장 없어 짧은 TTL로 충분.

`server.inventory` TTL/상한 없음: one-shot 메시지가 소실되면 다음 1시간 주기 재발행으로 자동 회복(CLAUDE.md #B).

`task.result` 정책 근거:
- 24h TTL: 운영자가 install 결과를 하루 안에 확인. 누적 적재 방지.
- 100K 상한: 머신당 install pending 최대 1건(`tasks` 부분 UNIQUE) + 결과 메시지 약 4KB라 1만 머신 X 1 buffer로 충분.
- Task RPC piggyback의 reply 자체는 별도 큐 declare 없이 `amq.rabbitmq.reply-to` pseudo-queue로 발행 — 본 표에 등재하지 않음 (큐 declare 불필요, broker 내부 처리).

`diagnostic.request` 정책 근거 (ADR 0004):
- engine 내부 (agent 발행 아님) — web POST /api/v1/diagnostics + 스케줄러 매일 03시 → 워커 소비.
- 24h TTL: 진단 1건 처리 cap 5분. 24h 안 미처리는 운영자 개입 신호.
- 100K 상한: 활성 서버 N대 + ad-hoc → 일일 N+α 발생. 100K로 충분.
- 워커 prefetch_count 1 — LLM 호출 동시 1건만 (rate limit 자연 throttle). adhoc/scheduled 큐 분리 안 함.

### DLQ 라우팅 트리거

다음 중 하나 발생 시 메시지가 자동으로 DLX로 routing되어 `*.dead` 큐에 쌓임:
- consumer 측 NAK (requeue=False) — 파싱 실패·DB 영구 장애 등
- 큐 TTL 만료 — metrics 72h, error 300s
- `x-max-length` 초과 — oldest 메시지부터

### 큐 인자 변경 절차

위 표의 TTL·max-length 등을 변경하면 broker가 기존 큐 재선언을 PRECONDITION_FAILED로 reject. consumer 재기동 전에 broker 측 큐 정의를 비워야 한다.

| 환경 | 절차 |
|------|------|
| dev | rabbitmq 컨테이너에 영속 볼륨 없음 → `docker compose up -d --force-recreate rabbitmq`만으로 큐 정의 소실. 이후 consumer 재기동 시 새 인자로 declare |
| prod | `rabbitmqadmin delete queue name=server.metrics` (변경 대상 큐) 후 consumer 재기동. rabbitmq_data 영속 볼륨 사용 시 큐 메타가 디스크에 남아 있으므로 명시적 삭제 필수 |

---

## 3. dev/prod 분기

### 차용 (이미 dev에 적용됨)

production 표준을 dev에도 적용 — namespace 격리·내구성 외 부담 없음.

| 항목 | 적용 |
|------|------|
| Vhost `/assessment` | `docker-compose.yml`의 `RABBITMQ_DEFAULT_VHOST` + `src/assessment_engine/config.py`의 `rabbitmq_vhost` + dev-up.sh가 VM 안 `/etc/assessment-agent.env`에 쓰는 `RABBITMQ_VHOST` 모두 `/assessment` |
| Exchange `assessment` (direct, durable) | 동일 |
| DLX `assessment.dlx` (direct, durable) | 동일 |
| 메시지 `delivery_mode=persistent` (2) | 에이전트 publish 측 설정 |

### 분기 유지 (dev 이점이 큼)

#### AMQP / TLS

| 환경 | 정책 |
|------|------|
| dev | plain AMQP, port 5672 |
| prod | AMQPS, port 5671, TLS 1.2+ + hostname verify, optional mTLS |

dev 이점:
- self-signed 인증서·내부 CA 발급·분배 부담 큼.
- `rabbitmqadmin` / 관리 UI 직접 디버깅 편의 손실 (TLS 핸드셰이크가 매번 가로막음).

#### 권한 모델 — dev 단일 user vs prod 4-user

dev: 단일 user `assessment` admin 권한 — declare 자유, 한 credential 유출 시 broker 무방비.

prod: 역할별 least-privilege 4 user.

| user | configure / write / read | 역할 |
|------|--------------------------|------|
| `agent-publisher` | `none / ^assessment$ / ^amq\.rabbitmq\.reply-to.*$` | 에이전트 publish + Task RPC reply 수신 |
| `worker-consumer` | `none / ^assessment$ / ^(server\.(inventory\|metrics\|error)\|task\.result)$` | consumer read·ack + reply publish, DLQ·declare 불가 |
| `dlq-handler` | `none / none / ^(server\.(inventory\|metrics\|error)\|task\.result)\.dead$` | DLQ read 전용 (운영 도구) |
| `topology-admin` | `.* / .* / .*` | 초기 셋업 1회만, 이후 회수 또는 user 삭제 |

dev에서 안 쓰는 이유: consumer가 기동 시마다 declare. 4-user 모델이면 매번 `topology-admin` 만들어 declare 후 회수하는 흐름이라 dev 사이클 부자연.

---

## 4. Production 전환 체크리스트

dev → production 시 #3 "분기 유지" 항목을 적용:

### 4.1 TLS 활성화
- 내부 CA 발급 + RabbitMQ 서버 인증서·키 배치
- `docker-compose.yml`의 rabbitmq 서비스에 TLS 설정 추가 (`rabbitmq.conf` 마운트 또는 환경변수)
- port `5671` 노출, `5672` 비활성화
- 에이전트 측 `RABBITMQ_TLS_*` env 활성화 + `/etc/assessment-agent/ca.pem` 분배

### 4.2 권한 분리
- `topology-admin` user 생성 (one-shot)
- `topology-admin`로 exchange / queue / DLX declare 1회 실행 후 권한 회수 또는 user 삭제
- `agent-publisher` / `worker-consumer` / `dlq-handler` user 생성 + 각 vhost permission set
- 엔진 consumer의 `RABBITMQ_USER`를 `worker-consumer`로 교체
- 에이전트 측 credentials를 `agent-publisher`로 배포

### 4.3 단일 broker → HA cluster 검토 (선택)
본 시스템 단일 인스턴스 정책은 `docs/tradeoffs.md` T11. SLA 요구가 강해질 때 재검토.

### 4.4 broker disk 용량 정책
`server.metrics` 큐의 `x-max-length` / TTL 보강 — 운영 환경 메시지 발생량·디스크 SLA에 맞춰 별도 결정.

---

## 관련 문서

- `docs/architecture/consumer.md` — 위 토폴로지를 코드(aio-pika)로 어떻게 declare·subscribe하는지 / 핸들러 / 멱등성 / DB 재시도
- `docs/architecture/agent.md` — 에이전트 측 publish 동작 / publisher confirm / retry
- `docs/operations/docker.md` — RabbitMQ 컨테이너 정의 / 헬스체크 / 환경변수
- `docs/operations/env.md` — `RABBITMQ_*` 환경변수 키 목록
- `docs/tradeoffs.md` T7 — 에이전트 broker 자동 재연결 (이미 구현됨)