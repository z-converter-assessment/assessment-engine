# CLAUDE.md

## 프로젝트 개요
ZConverter Cloud Assessment Portal.
고객사 내부 네트워크의 각각의 호스트 인벤토리를 수집·저장하는 B2B 내부 포털.

### 배포 시나리오
고객사 네트워크 내에 서버 엔진(web + consumer + MQ + DB)이 설치된다.
네트워크 내 각 서버에는 **C99/C++03 기반 에이전트**가 탑재되어 메트릭을 수집하고,
**MQ에 직접 발행**한다. Consumer가 이를 소비해 DB에 저장한다.

## 테이블 구조

### server_inventory
인벤토리 메시지 수신 시 `machine_id` 기준으로 upsert. 정적 서버 스펙을 보관.

| 컬럼 | 타입 | 제약 | 설명 |
|------|------|------|------|
| id | integer | PK, autoincrement | |
| machine_id | String(64) | unique, not null, index | `/etc/machine-id` — 불변 식별자 |
| hostname | String(255) | not null, index | 최신 hostname (변경 가능) |
| os_id | String(64) | **nullable** | 배포판 식별자 — `/etc/os-release` 없는 구형 시스템 대비 |
| os_version | String(64) | **nullable** | 배포판 버전 — 동일 |
| kernel_version | String(64) | not null | X.Y.Z (`uname(2)` 보장) |
| cpu_cores | integer | not null | 온라인 코어 수 (`sysconf` 보장) |
| cpu_model | String(255) | **nullable** | CPU 모델명 — x86_64 파싱 실패 대비 |
| mem_total_mb | bigint | not null | 전체 물리 메모리 (MB) |
| disks | JSONB | not null | `[{name, size_bytes}]` — 빈 배열 유효 |
| disk_usage | JSONB | not null | `[{mount, total_mb, used_mb, avail_mb}]` — 빈 배열 유효 |
| ip_internal | JSONB | not null | `["10.0.1.15", …]` — 빈 배열 유효 |
| ip_external | JSONB | **nullable** | `null`=수집 실패, `[]`=external IP 없음 |
| agent_version | String(32) | not null | |
| created_at | timestamp with tz | not null, server_default | 최초 등록 |
| updated_at | timestamp with tz | not null | 인벤토리 갱신 시 직접 수정 |

### server_metrics

| 컬럼 | 타입 | 제약 | 설명 |
|------|------|------|------|
| id | integer | PK, autoincrement | |
| server_id | integer | FK → servers.id, not null, index | |
| collected_at | timestamp with tz | not null, index | 에이전트 수집 시각 |
| cpu_user_pct | float | not null | 사용자 공간 CPU % |
| cpu_system_pct | float | not null | 커널 공간 CPU % |
| cpu_iowait_pct | float | not null | I/O 대기 CPU % |
| mem_used_mb | integer | not null | 사용 중 메모리 (MB) |
| mem_available_mb | integer | **nullable** | CentOS 7.0~7.1 fallback 실패 시 null |
| swap_used_mb | integer | not null | 스왑 사용량 (MB) |
| disk_read_iops | integer | **nullable** | 기동 직후 첫 전송 delta 없음 |
| disk_write_iops | integer | **nullable** | 동일 |
| disk_read_bytes | bigint | **nullable** | 동일 |
| disk_write_bytes | bigint | **nullable** | 동일 |
| disk_usage | JSONB | not null | `[{mount, usage_pct}]` — 빈 배열 유효 |
| net_rx_bytes | bigint | **nullable** | 기동 직후 첫 전송 delta 없음 |
| net_tx_bytes | bigint | **nullable** | 동일 |
| load_1m | float | not null | 1분 로드 평균 |
| created_at | timestamp with tz | not null, server_default | DB 삽입 시각 |

## ORM 설계 원칙
- DTO(dataclass)와 ORM 모델은 분리, 변환은 repository 구현체 책임
- servers 식별 기준: `machine_id` (inventory upsert)

## Consumer 설계 원칙
- aio-pika 기반 순수 비동기 컨슈머 (FastAPI와 독립 프로세스)
- FastAPI와 동일한 세션(`session.py`) 공유
- 파싱 실패: raise → nack(requeue=False) → DLX → DLQ
- DB 실패: 지수 백오프 3회 재시도 후 raise → nack(requeue=False) → DLX → DLQ
- TTL 만료(60초): 브로커가 자동으로 DLX → DLQ
- `error` 메시지: 파싱 후 로깅만. DB 저장 없음 (추후 알림 연동 고려)

### MQ 토폴로지
Exchange: `assessment` (topic, durable) / DLX: `assessment.dlx`
큐 이름 = routing key 이름으로 통일.

| routing key | 큐 | DLQ | 처리 |
|-------------|-----|-----|------|
| `server.inventory` | `server.inventory` | `server.inventory.dead` | servers upsert |
| `server.metrics` | `server.metrics` | `server.metrics.dead` | metric_snapshots insert |
| `server.error` | `server.error` | `server.error.dead` | 로깅 (저장 없음) |

### 공통 메타데이터 (모든 메시지 타입 포함)
| 필드 | 타입 | 설명 |
|------|------|------|
| `message_type` | string | `"inventory"` / `"metrics"` / `"error"` |
| `agent_version` | string | 빌드 시 define (e.g. `"1.0.0"`) |
| `collected_at` | string | ISO 8601 UTC (e.g. `"2026-04-23T14:30:00Z"`) |
| `hostname` | string | 서버 식별자 |
| `message_id` | string | UUID v4 |

### inventory 페이로드 (`server.inventory`)
에이전트 기동 시 1회 발송. servers 테이블 upsert (`machine_id` 기준).

```json
{
  "message_type": "inventory",
  "agent_version": "1.0.0",
  "collected_at": "2026-04-23T14:30:00Z",
  "hostname": "web-server-01",
  "message_id": "550e8400-e29b-41d4-a716-446655440000",
  "machine_id": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4",
  "os_id": "ubuntu",
  "os_version": "22.04",
  "kernel_version": "5.15.0",
  "cpu_cores": 4,
  "cpu_model": "Intel(R) Xeon(R) Platinum 8375C CPU @ 2.90GHz",
  "mem_total_mb": 16384,
  "disks": [
    { "name": "vda", "size_bytes": 32212254720 },
    { "name": "vdb", "size_bytes": 107374182400 }
  ],
  "disk_usage": [
    { "mount": "/", "total_mb": 30720, "used_mb": 12800, "avail_mb": 17920 },
    { "mount": "/data", "total_mb": 102400, "used_mb": 51200, "avail_mb": 51200 }
  ],
  "ip_internal": ["10.0.1.15", "172.16.0.3"],
  "ip_external": ["54.123.45.67"]
}
```

### metrics 페이로드 (`server.metrics`)
1분 주기 발송. hostname → server_id 조회 후 metric_snapshots insert.

```json
{
  "message_type": "metrics",
  "agent_version": "1.0.0",
  "collected_at": "2026-04-23T14:31:00Z",
  "hostname": "web-server-01",
  "message_id": "550e8400-e29b-41d4-a716-446655440001",
  "cpu_user_pct": 23.5,
  "cpu_system_pct": 4.2,
  "cpu_iowait_pct": 1.8,
  "mem_used_mb": 12288,
  "mem_available_mb": 4096,
  "swap_used_mb": 0,
  "disk_read_iops": 150,
  "disk_write_iops": 85,
  "disk_read_bytes": 6291456,
  "disk_write_bytes": 3145728,
  "disk_usage": [
    { "mount": "/", "usage_pct": 80.0 },
    { "mount": "/data", "usage_pct": 20.0 }
  ],
  "net_rx_bytes": 1048576,
  "net_tx_bytes": 524288,
  "load_1m": 1.25
}
```

### error 페이로드 (`server.error`)
수집·발행 실패 시 발송. 로깅만 처리 (DB 저장 없음).

```json
{
  "message_type": "error",
  "agent_version": "1.0.0",
  "collected_at": "2026-04-23T14:31:00Z",
  "hostname": "web-server-01",
  "message_id": "550e8400-e29b-41d4-a716-446655440002",
  "error_code": "COLLECT_MEMINFO_FAILED",
  "error_message": "failed to open /proc/meminfo: Permission denied",
  "failed_component": "collect",
  "timestamp": "2026-04-23T14:31:00.123Z"
}
```

# Query API Design

## Base URL
GET /api/v1/

## Endpoints

### 서버 목록
GET /servers
- query: page, limit, search(hostname), is_online(bool)
- response: id, hostname, os_name, os_version, cpu_cores,
            memory_total_mb, internal_ip, is_online, last_seen_at

### 서버 단건 (1차 화면)
GET /servers/{server_id}
- response:
    system: hostname, os, kernel, cpu_cores, cpu_model,
            memory_total_mb, last_seen_at
    disks[]: device, size_bytes, type, fstype, mountpoint
    networks[]: interface, address, scope(Internal|External)

### 스토리지 상세 (2차 화면 상단)
GET /servers/{server_id}/storage
- response:
    summary: physical_disks, partitions, uses_lvm, root_fs
    disks[]: device, type, size_bytes, fstype, mountpoint, uuid
    lvm:
      pvs[]: pv_name, vg_name, size_bytes, free_bytes
      vgs[]: vg_name, size_bytes, free_bytes, lv_count
      lvs[]: lv_name, vg_name, lv_path, size_bytes, mountpoint
    filesystems[]: filesystem, fstype, size_bytes, used_bytes,
                   use_percent, mountpoint,
                   status(ok|needs_check|danger)

### 네트워크 상세 (2차 화면 하단)
GET /servers/{server_id}/network
- response:
    summary: primary_nic, speed_mbps, gateway, dns_servers[]
    interfaces[]: name, address, mac, speed_mbps, duplex,
                  driver, is_up, scope
    routes[]: destination, gateway, interface, is_default
    dns[]: entry_type(nameserver|search), value

### 컬렉션 상태 (2차 화면 뱃지)
GET /servers/{server_id}/collection-status
- response:
    items[]: category, status(ok|needs_check|failed), source_path, message

### 메트릭 최신값 (UI 카드 게이지)
GET /servers/{server_id}/metrics/latest
- response:
    cpu_usage_percent, memory_usage_percent,
    swap_usage_percent, collected_at
    disks[]: mountpoint, use_percent, status
    interfaces[]: name, rx_bps, tx_bps

### 메트릭 시계열 (차트)
GET /servers/{server_id}/metrics
- query: metric_type, dimension, start, end,
         bucket(5m|1h|1d), agg(avg|max|p95)
- response:
    series[]: time, value
- metric_type 예시:
    cpu.usage_percent
    memory.usage_percent
    disk.read_iops / disk.write_iops      (dimension=장치명)
    fs.usage_percent                       (dimension=mountpoint)
    net.rx_bytes_per_sec                   (dimension=NIC명)

## 공통 규칙
- 인증: Bearer token (헤더)
- 날짜: ISO 8601 (UTC)
- 에러: { code, message, detail }
- 페이지: { data[], total, page, limit }
- server_id: UUID

## 실행 환경
- 메인 스택: docker compose (web, consumer, rabbitmq, postgres, redis)
- 실제 에이전트: C99/C++03 바이너리, 고객사 네트워크 내 각 서버에 배포

## Redis 역할
- 최신 상태 캐싱: 서버별 최신 인벤토리·메트릭 스냅샷을 캐싱해 DB 조회 부하 절감
- 태스크 멱등성: `message_id` 기반 중복 처리 방지
- 온라인 TTL: 마지막 메트릭 수신 시각을 TTL 키로 관리해 서버 온라인 상태 판단
- PUB/SUB: 메트릭 수신 이벤트를 web 레이어로 실시간 전달하기 위한 채널

## TimescaleDB
PostgreSQL에 TimescaleDB 확장을 사용한다.
`server_metrics` 테이블을 hypertable로 관리해 시계열 조회·집계 성능을 확보한다.

## 환경변수
루트 `.env`에서 주입. 키 목록은 `.env.example` 참조.

## 브랜치 전략
| 브랜치 | 용도 |
|--------|------|
| main | 배포용. 직접 push 금지 |
| develop | 개발 통합. PR로만 머지 |
| feature/xxx | 기능 개발 |
| fix/xxx | 버그 수정 |
| chore/xxx | 설정 변경 |

## 커밋 컨벤션
설명은 한글로 작성

| 타입 | 설명 |
|------|------|
| feat | 새로운 기능 추가 |
| fix | 버그 수정 |
| chore | 설정, 패키지 변경 |
| refactor | 리팩토링 |
| test | 테스트 코드 |
