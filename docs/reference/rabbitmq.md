# RabbitMQ

정책: CLAUDE.md #B. 본 문서는 broker 토폴로지·큐 정책·dev/prod 분기 단일 진실. 코드 동작은 `docs/reference/consumer.md`.

---

## 1. 본 시스템 결정

vhost: `assessment` (무슬래시) 단일 사용. broker 한 대를 다른 도메인 시스템과 나눠 쓸 때만 추가 vhost 도입. AMQP URL은 `amqp://user:pass@host:port/assessment` 형식 — 이름에 `/`가 없어 인코딩 무영향(config.py 가 슬래시 포함 vhost 를 `%2F`로 자동 인코딩하는 방어 로직은 유지).

권한 모델: RabbitMQ는 `(user, vhost)` 쌍에 configure/write/read 3비트를 정규식 패턴으로 부여. dev는 단일 user `assessment`가 셋 모두 보유. prod는 4-user least-privilege로 분리 (#3 dev/prod 분기).

도구 일반론(vhost·권한 비트 의미)은 RabbitMQ 공식 문서.

---

## 2. 본 시스템 토폴로지

### 정의

| 항목 | 값 |
|------|-----|
| Vhost | `assessment` |
| Collector exchange | `assessment` (direct, durable) — inventory/metrics/error |
| Collector DLX | `assessment.dlx` (direct, durable) |
| Task exchange | `assessment.tasks` (direct, durable) — task.install/task.result |
| Task DLX | `assessment.tasks.dlx` (direct, durable) |
| prefetch_count | 10 |
| 메시지 delivery_mode | `persistent` (2) — 모든 발행 측 설정 |

`prefetch_count` 10 근거:
- `global=false`(consumer 단위 상한) + 큐 4개를 한 채널에서 소비 -> 프로세스 미ack 배달 상한 = 10 X 4 = 40.
- 배달 하나가 핸들러 task 하나라 종료 시 drain 이 기다리는 in-flight 상한도 40 — 올리면 종료 예산 안에 못 끝낼 수 있다 (drain 절차는 `docs/reference/consumer.md` "Disposability" 절).
- DB 재시도에 걸린 메시지는 재시도 창 동안 자기 슬롯을 점유한다 (창 길이는 같은 문서 "DB 재시도 정책" 절).

### 큐 정책

| 큐 | exchange | binding routing key | 발행 주체 | DLQ | TTL | x-max-length |
|----|----------|---------------------|-----------|-----|-----|--------------|
| `server.inventory` | `assessment` | `server.inventory` | 원격 호스트 | `server.inventory.dead` | 없음 (주기 재발행으로 보강) | 없음 |
| `server.metrics` | `assessment` | `server.metrics` | 원격 호스트 | `server.metrics.dead` | 72h | 1,000,000 |
| `server.error` | `assessment` | `server.error` | 원격 호스트 | `server.error.dead` | 300s | 없음 |
| `worker.result` | `assessment.tasks` | `task.result` | 원격 호스트 | `worker.result.dead` | 24h | 100,000 |
| `agent.tasks.<agent_id>` | `assessment.tasks` | `task.install.<agent_id>` | 엔진 (web) | (없음) | `install_task_deadline_sec` (기본 3600s) | `_TASK_QUEUE_MAX_LEN` (100) + `x-overflow=reject-publish` |

`server.metrics` 정책 근거:
- 72h TTL: 1분 주기 발행 + consumer/DB 단기 장애(최대 3일) 내 회복 시 누적 메시지 정상 처리.
- 1M 메시지 상한: 약 3KB X 1M = 약 3GB 디스크/메모리 -> broker 폭주 방어.
- 초과 시 oldest 메시지부터 DLX(`server.metrics.dead`)로 routing.

`server.error` 300s TTL: 알림용 노이즈 방지. DB 저장 없어 짧은 TTL로 충분.

`server.inventory` TTL/상한 없음: one-shot 메시지가 소실되면 다음 1시간 주기 재발행으로 자동 회복(CLAUDE.md #B).

`worker.result` 정책 근거:
- 24h TTL: 운영자가 install 결과를 하루 안에 확인. 누적 적재 방지.
- 100K 상한: 머신당 install pending 최대 1건(`tasks` 부분 UNIQUE) + 결과 메시지 약 4KB라 1만 머신 X 1 buffer로 충분.

`agent.tasks.<agent_id>` 정책 근거:
- 머신별 전용 큐 — `task.install.<agent_id>` routing key 로 정확히 해당 머신만 배달.
- 엔진이 task 발행 시점에 동적 declare (수신 측은 declare 권한 없음 가정). idempotent.
- `x-message-ttl` = `install_task_deadline_sec` — 엔진 `tasks.deadline_at` 과 같은 창 (CLAUDE.md #F10). 머신이 그 사이 consume 못 하면 만료 (해당 머신 오프라인). DLX 없음 — 만료 메시지는 drop 되고, 대응하는 task 의 상태 전이는 `docs/explanation/products/install-task.md`.
- max-length + `x-overflow=reject-publish`: 버퍼 폭주 차단. publish 시 publisher 측이 error 인지 (best-effort 운영 시그널).
- prod 에서 DLX 정책 보강은 별도 ADR.

### DLQ 라우팅 트리거

다음 중 하나 발생 시 메시지가 자동으로 DLX로 routing되어 `*.dead` 큐에 쌓임:
- consumer 측 NAK (requeue=False) — 파싱 실패·DB 영구 장애 등
- 큐 TTL 만료 — 위 큐 정책 표의 TTL 컬럼
- `x-max-length` 초과 — oldest 메시지부터

### 큐 인자 변경 절차

위 표의 TTL·max-length 등을 변경하면 broker가 기존 큐 재선언을 PRECONDITION_FAILED로 reject. dev·prod 공통으로 변경 대상 큐를 명시 삭제한 뒤 consumer 를 재기동하면 새 인자로 declare 된다.

```bash
docker compose exec rabbitmq rabbitmqctl -p assessment delete_queue server.metrics
docker compose restart consumer
```

---

## 3. dev/prod 분기

### 차용 (이미 dev에 적용됨)

production 표준을 dev에도 적용 — namespace 격리·내구성 외 부담 없음.

| 항목 | 적용 |
|------|------|
| Vhost `assessment` | `docker-compose.yml` 의 `RABBITMQ_DEFAULT_VHOST` + `src/assessment_engine/config.py` 의 `rabbitmq_vhost` + agent 의 `RABBITMQ_VHOST` 모두 `assessment`(무슬래시) |
| Collector exchange `assessment` (direct, durable) | 동일 |
| Collector DLX `assessment.dlx` (direct, durable) | 동일 |
| Task exchange `assessment.tasks` (direct, durable) | 동일 (web 측 lifespan + consumer 측 main.py 양쪽 declare, idempotent) |
| Task DLX `assessment.tasks.dlx` (direct, durable) | 동일 (consumer 측 declare) |
| 메시지 `delivery_mode=persistent` (2) | 모든 발행 측 설정 |

### 분기 유지 (dev 이점이 큼)

#### AMQP / TLS

| 환경 | 정책 |
|------|------|
| dev | plain AMQP, port 5672 |
| prod | AMQPS, port 5671, TLS 1.2+ + hostname verify, optional mTLS |

dev 이점:
- self-signed 인증서·내부 CA 발급·분배 부담 큼.
- `rabbitmqadmin` / 관리 UI 직접 디버깅 편의 손실 (TLS 핸드셰이크가 매번 가로막음).

#### 권한 모델 — dev 단일 user vs prod least-privilege

dev: 단일 user `assessment` 가 collector·worker·engine 역할 모두 보유 (declare 자유). 한 credential 유출 시 broker 무방비.

prod: 역할별 least-privilege 분리. 발행·소비 채널 별 권한.

| user | configure / write / read | 역할 |
|------|--------------------------|------|
| `agent-publisher` | `none / ^assessment$ / none` | 원격 호스트 collector 발행 (inventory/metrics/error) |
| `agent-worker` | `none / ^assessment\.tasks$ / ^agent\.tasks\..*$` | 원격 호스트 worker 가 `agent.tasks.<agent_id>` consume + `assessment.tasks` 에 `task.result` publish |
| `engine-publisher` | `none / ^assessment\.tasks$ / none` | 엔진 web 이 `task.install` publish |
| `engine-consumer` | `^agent\.tasks\..* / ^assessment(\.tasks)?$ / ^(server\..*\|worker\.result\|agent\.tasks\..*)$` | 엔진 consumer read·ack + 머신별 큐 동적 declare |
| `dlq-handler` | `none / none / ^.*\.dead$` | DLQ read 전용 (운영 도구) |
| `topology-admin` | `.* / .* / .*` | 초기 셋업 1회만, 이후 회수 또는 user 삭제 |

dev 에서 안 쓰는 이유: 큐 동적 declare (`agent.tasks.<agent_id>`) 가 매 task 발행마다 발생. dev 사이클에서 매번 권한 분리 적용은 부자연.

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
- `topology-admin` 로 exchange / queue / DLX declare 1회 실행 후 권한 회수 또는 user 삭제
- `agent-publisher` / `agent-worker` / `engine-publisher` / `engine-consumer` / `dlq-handler` user 생성 + 각 vhost permission set
- 엔진 consumer 의 `RABBITMQ_USER` 를 `engine-consumer` 로 교체. 엔진 web 의 publish 채널은 `engine-publisher`
- 원격 호스트 collector credentials 는 `agent-publisher`, worker credentials 는 `agent-worker` 로 배포 (`RABBITMQ_USER` vs `RABBITMQ_WORKER_USER` 분리)

### 4.3 단일 broker → HA cluster 검토 (선택)
본 시스템 단일 인스턴스 정책은 `docs/explanation/tradeoffs.md` T11. SLA 요구가 강해질 때 재검토.

### 4.4 broker disk 용량 정책
`server.metrics` 큐의 `x-max-length` / TTL 보강 — 운영 환경 메시지 발생량·디스크 SLA에 맞춰 별도 결정.

---

## 관련 문서

- `docs/reference/consumer.md` — 위 토폴로지를 코드(aio-pika)로 어떻게 declare·subscribe하는지 / 핸들러 / 멱등성 / DB 재시도
- `docs/reference/contracts/agent-data.md` — 에이전트 측 publish 동작 / publisher confirm / retry
- `docs/reference/docker.md` — RabbitMQ 컨테이너 정의 / 포트 / 볼륨
- `docs/reference/contracts/env.md` — `RABBITMQ_*` 환경변수 키 목록
- `docs/explanation/tradeoffs.md` T7 — 에이전트 broker 자동 재연결 (이미 구현됨)