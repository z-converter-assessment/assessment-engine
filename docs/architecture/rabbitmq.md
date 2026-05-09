# RabbitMQ

본 문서는 RabbitMQ broker 운영의 단일 진실. 코드(`src/assessment_engine/consumer/main.py`)·인프라(`docker-compose.yml`)·계약(에이전트 ↔ 엔진)에 흩어진 broker 관련 결정을 한 곳에 모은다. 토폴로지 코드 동작 관점은 `docs/architecture/consumer.md`.

---

## 1. 개념

### vhost (virtual host)

단일 broker 안에서 논리적으로 분리된 namespace. HTTP virtual host와 발상 비슷.

| 격리 단위 | 내용 |
|----------|------|
| Exchange / Queue / Binding | 다른 vhost와 이름 겹쳐도 충돌 없음 — `/assessment` vhost의 `assessment` exchange와 `/`(기본) vhost의 `assessment` exchange는 별개 |
| User permission | user 단위가 아니라 `(user, vhost)` 쌍으로 read·write·configure 권한 부여 |
| 메시지 흐름 | vhost 간 메시지 흐름 없음. 같은 broker라도 통신 안 됨 |
| 운영 사고 격리 | 한 vhost의 큐 폭주가 다른 vhost에 직접 영향 없음 (자원 한계 내) |

AMQP URL 표기: `amqp://user:pass@host:port/<vhost>`. vhost 이름에 `/`가 포함되면 `%2F`로 인코딩 — 본 시스템 `broker_url`은 `amqp://assessment:assessment@rabbitmq:5672/%2Fassessment` (config.py에서 자동 처리).

기본 vhost: `/` (슬래시 한 글자). 아무 설정 안 하면 모든 user가 여기로 접속.

본 시스템에서의 의의: 현재 broker는 `/assessment` 단일 vhost만 운영하고 그 안의 모든 메시지(inventory/metrics/error)는 호스트 인벤토리·메트릭 수집 도메인. 같은 broker 위에 별도 vhost를 추가하면 다른 도메인 시스템(알림 큐·작업 큐·결제 이벤트 등)이 namespace·permission·자원 격리된 상태로 공존 가능. 즉 vhost = broker 한 대를 여러 시스템이 안전하게 나눠 쓰는 격리 단위.

### 권한 모델

RabbitMQ는 user마다 vhost 단위로 3가지 권한 비트 부여. 각 비트는 정규식 패턴으로 적용 대상 제한.

| 권한 비트 | 의미 |
|----------|------|
| configure | exchange / queue / binding을 declare(생성·수정·삭제) |
| write | exchange에 메시지 publish |
| read | queue에서 메시지 consume (read + ack) |

dev 단일 user `assessment`는 셋 다 가짐 (admin). production은 #3에서 4-user로 분리.

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

| routing key | 큐 | DLQ | TTL | x-max-length |
|-------------|-----|-----|-----|--------------|
| `server.inventory` | `server.inventory` | `server.inventory.dead` | 없음 (one-shot) | 없음 |
| `server.metrics` | `server.metrics` | `server.metrics.dead` | 72h | 1,000,000 |
| `server.error` | `server.error` | `server.error.dead` | 300s | 없음 |

`server.metrics` 정책 근거:
- 72h TTL: 1분 주기 발행 + consumer/DB 단기 장애(최대 3일) 내 회복 시 누적 메시지 정상 처리.
- 1M 메시지 상한: ~3KB × 1M = ~3GB 디스크/메모리 → broker 폭주 방어.
- 초과 시 oldest 메시지부터 DLX(`server.metrics.dead`)로 routing.

`server.error` 300s TTL: 알림용 노이즈 방지. DB 저장 없어 짧은 TTL로 충분.

`server.inventory` TTL/상한 없음: one-shot 메시지로 소실 시 에이전트 재시작 전까지 복구 불가. 미팅 의제 A(주기 재발행) 채택 시 보강.

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
| Vhost `/assessment` | `docker-compose.yml`의 `RABBITMQ_DEFAULT_VHOST` + `src/assessment_engine/config.py`의 `rabbitmq_vhost` + Vagrantfile의 `RABBITMQ_VHOST` 모두 `/assessment` |
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

dev: 단일 user `assessment` (admin 권한). publish · consume · queue/exchange declare 모두 자유. credential 한 개 유출 시 broker 전체 무방비.

prod: 각 역할에 필요한 권한 비트만 부여 (least privilege).

| user | configure / write / read | 역할 |
|------|--------------------------|------|
| `agent-publisher` | `none / ^assessment$ / none` | 에이전트가 사용. exchange `assessment`에 inventory/metrics/error publish만. queue declare 불가, consume 불가 |
| `worker-consumer` | `none / none / ^server\.(inventory\|metrics\|error)$` | 엔진 consumer가 사용. 정상 큐 read·ack만. publish·declare 불가 |
| `dlq-handler` | `none / none / ^server\.(inventory\|metrics\|error)\.dead$` | DLQ 메시지 분석·재처리 도구용 (별도 운영 도구). DLQ만 read |
| `topology-admin` | `.* / .* / .*` | 시스템 초기 셋업 시 1회만 사용. exchange / queue / DLX declare 후 권한 회수 또는 user 삭제. 평시 credential 노출 없음 |

왜 4개로 나누나 — 침해 시 blast radius 제한:
- agent-publisher 유출 → publish만 가능. 큐 삭제·정상 큐 read·DLQ 조작 모두 불가
- worker-consumer 유출 → 정상 큐 read만 가능. publish·DLQ 조작 불가
- dlq-handler 유출 → DLQ read만 가능
- topology-admin은 1회 사용 후 회수되므로 평시 credential 미노출

dev에서 안 쓰는 이유:
- 현재 `src/assessment_engine/consumer/main.py`가 기동 시마다 exchange / queue / DLX를 declare. dev consumer가 admin이라 자유롭게 가능.
- production은 `topology-admin`이 one-shot bootstrap으로 declare 후 회수 → 이후 `worker-consumer`는 declare 권한 없이 이미 존재하는 큐에만 connect.
- dev에서 큐 인자 변경 시 컨테이너 재생성 + consumer 재기동만으로 새 인자 declare됨. 4-user 모델이면 매번 `topology-admin` user 만들어 declare 후 회수하는 흐름이라 dev 사이클이 부자연스러움.

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
본 시스템 단일 인스턴스 정책은 `docs/adr/tradeoffs.md` T11. SLA 요구가 강해질 때 재검토.

### 4.4 broker disk 용량 정책
`server.metrics` 큐의 `x-max-length` / TTL 보강 — 운영 환경 메시지 발생량·디스크 SLA에 맞춰 별도 결정.

---

## 관련 문서

- `docs/architecture/consumer.md` — 위 토폴로지를 코드(aio-pika)로 어떻게 declare·subscribe하는지 / 핸들러 / 멱등성 / DB 재시도
- `docs/architecture/agent.md` — 에이전트 측 publish 동작 / publisher confirm / retry
- `docs/operations/docker.md` — RabbitMQ 컨테이너 정의 / 헬스체크 / 환경변수
- `docs/operations/env.md` — `RABBITMQ_*` 환경변수 키 목록
- `docs/adr/tradeoffs.md` T7 — 에이전트 broker 자동 재연결 (이미 구현됨)