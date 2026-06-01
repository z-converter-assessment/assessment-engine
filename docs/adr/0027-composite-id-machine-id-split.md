# ADR 0027 — composite_id 단일 식별 + machine_id 표시 분리

상태: Accepted (2026-05-26)

ADR 0022 supersede (host_id 단일 식별 결정 대체). agent v4 (assessment-agent #8 / #10 / #11) payload contract 변경 반영.

## 배경

ADR 0022 는 호스트 식별을 `host_id` 단일 (64자, agent 가 `SHA-256(primary_MAC | machine_id)` 합성) 으로 잡았다. agent v4 가 식별 체계를 둘로 분리했다:

- `machine_id` — raw machine-id (Linux `/etc/machine-id` 32 hex, Windows `MachineGuid`).
- `composite_id` — `SHA-256(machine_id + "\n" + 정렬·dedup MAC 들)` 64자. 이미지 클론 환경에서 machine_id 중복을 구분하는 식별 주키.

agent 는 모든 수집 메시지(inventory/metrics/error)에 두 필드를 함께 발행하고, `host_id` 키는 더 이상 보내지 않는다. 엔진이 `host_id` required 를 유지하면 v4 메시지가 Pydantic 검증에서 전량 reject 된다.

## 결정

- 엔진 식별 단일 키 = `composite_id`. ADR 0022 `host_id` 의 모든 역할을 대체:
  - `server_inventory.composite_id` UNIQUE (식별 주키)
  - URL `public_id`(UUID) 매핑 대상은 동일 (정수 PK·public_id 분리 불변)
  - task 라우팅·큐 키 (`task.install.{composite_id}` / `agent.tasks.{composite_id}`)
- `machine_id` = 표시 전용 (nullable). 식별·라우팅 미사용. 서버 세부 화면 "Machine ID" 표시. `composite_id` 는 "Host ID" 표시.
- 엔진 코드·DB·문서 전반 `host_id` -> `composite_id` 전면 rename (내부 이름과 wire 계약 이름 일치).

## 식별자 분리 (ADR 0022 표 갱신)

| 식별자 | 타입 | 역할 |
|--------|------|------|
| `server_inventory.id` | `bigint autoincrement PK` | DB internal — FK 대상 (시계열 5 테이블 `server_id`) |
| `server_inventory.composite_id` | `varchar(64) UNIQUE NOT NULL` | agent 매칭·식별 단일 키 — SHA-256 composite hash |
| `server_inventory.machine_id` | `varchar(64) NULL` | raw machine-id 표시 전용 |
| `server_inventory.public_id` | `uuid UNIQUE NOT NULL` | URL 노출 (`/servers/{public_id}`) |
| `server_inventory.hostname` | `varchar(255)` | display (UNIQUE X) |

## 스키마 전환

- revision `b3e1d7f9a2c4`: `server_inventory.host_id` -> `composite_id` rename + UNIQUE 제약 rename, `machine_id` 컬럼 신규(nullable), `tasks.target_host_id` -> `target_composite_id` rename(+ index). `server_inventory_history` 는 식별 컬럼 미러 제외(server_id 충분)라 무변경.

## MQ 토폴로지 (ADR 0022 갱신)

- routing key `task.install.{composite_id}` / queue `agent.tasks.{composite_id}` — engine 동적 declare. agent v4(#11) 도 큐 이름을 composite_id 기반으로 전환해 라우팅 키와 일치.
- `task.result` 는 worker 가 composite hash 미산출 — 엔진 `TaskResultInput.composite_id` nullable override. 결과 매칭은 `task_id`. (agent worker 는 현재 `machine_id` 키 발행하나 엔진 `extra=ignore` 무시.)

## Windows agent 합류 (v4)

- `os_family="windows"` 분기. 수치 데이터는 agent 가 Linux 계약 단위로 정규화 발행 (cpu FILETIME 100ns /100000 -> 10ms tick, disk bytes /512 -> sectors, mem bytes /1024 -> kB). raw 아님 — 엔진 OS 무관 단일 처리.
- 플랫폼 부재 필드 null/0: `load_1m/5m/15m`·`mem_buffers_kb`·`mem_cached_kb`·`listen_ports[].uid` = null, `cpu_stat.{nice,iowait,irq,softirq,steal}` = 0. 엔진 inbound DTO nullable/0 수용. `listen_ports[].uid` 를 required `int` 에서 `int | None` 으로 완화 (Windows reject 방지).

## 트레이드오프

- composite_id 는 MAC 변동(NIC 추가/제거)시 바뀐다 — DB 상 새 호스트로 취급. machine_id 로 라우팅하면 클론된 두 호스트가 큐를 공유해 task 가 엉뚱한 호스트로 갈 위험이 있어, 클론 식별이 안정적인 composite_id 를 라우팅 키로 채택.
