# ADR 0022 — 호스트 식별자 분리

상태: Superseded by ADR 0027 (2026-05-26)

원본 Accepted (2026-05-23). agent v4 계약으로 `host_id` 단일 식별이 `composite_id` 단일 식별 + `machine_id` 표시 분리로 대체됨 — 식별자 표·MQ 토폴로지는 ADR 0027 이 재정의. 아래 본문은 당시 결정 기록으로 보존 (ADR 정정 금지 원칙).

## 결정

호스트 단위 식별자 3 분리 + display field 1:

| 식별자 | 타입 | 역할 |
|--------|------|------|
| `server_inventory.id` | `bigint autoincrement PRIMARY KEY` | DB internal — FK 대상 (시계열 5 테이블 `server_id`) |
| `server_inventory.host_id` | `char(64) UNIQUE NOT NULL` | agent 매칭 — composite hash (SHA-256 hex) |
| `server_inventory.public_id` | `uuid UNIQUE NOT NULL` | URL 노출 (`/servers/{public_id}`) |
| `server_inventory.hostname` | `varchar(255)` | display (UNIQUE 제약 X) |

## host_id 합성 — agent 책임

```
SHA-256(primary_MAC || "|" || /etc/machine-id) → hex 64 char
```

primary MAC 규약 — 첫 번째 non-loopback + `state=up` NIC.

## MQ 토폴로지

- routing key `task.install.{host_id}`
- queue `agent.tasks.{host_id}` — engine 동적 declare
- DLQ `task.install.{host_id}.dead`

## 시계열 FK

5 hypertable (`server_metrics` / `server_disk_io` / `server_mount_usage` / `server_net_io` / `server_inventory_history`) FK = `server_id bigint` (FK → `server_inventory.id`).

## tasks audit

`tasks.target_host_id char(64)` — server_inventory 삭제 시 lookup 유지.
