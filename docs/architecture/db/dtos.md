# DB DTO 카탈로그

정책: CLAUDE.md #C2. Repository·Service 경계 dataclass — ORM 모델 직접 노출 금지.

## Inbound DTO (`inbound.py`) — Service → Repository

| DTO | 용도 | 필드 정책 |
|-----|------|-----------|
| `ServerInventoryCreate` | inventory upsert | JSONB 컬럼(`disks`/`mounts`/`services`/`listen_ports`)은 `list[dict]` — JSONB 직렬화에 자연 |
| `ServerMetricCreate` | metrics 1건 | nested `list[DiskIoEntry]` / `list[MountUsageEntry]` / `list[NetIoEntry]` — 시계열 4테이블 행 매핑이라 컴파일 타임 타입 보장. boot_time/agent_started_at 포함 |
| `DiskIoEntry` / `MountUsageEntry` / `NetIoEntry` | 시계열 행 nested | dict 키 오타 방지 — mapper 단계에서 차단. INSERT 시 `dataclasses.asdict(entry)`로 풀어쓰기 |
| `TaskCreate` | task 발행 | target_server_id / target_agent_id / task_type / params (JSONB) |
| `TaskResultUpdate` | task 결과 수신 | public_id / status / failure_reason / exit_code / duration_ms / stdout_tail / stderr_tail / completed_at |

## Outbound DTO (`outbound.py`) — Repository → Service

모두 raw 단위 (P1) — KB·bytes·jiffies·sectors 그대로. 변환은 service.

### 서버 표시 raw

| DTO | 용도 |
|-----|------|
| `ServerSummary` | 서버 목록 (큰 JSONB·텍스트 제외 명시 SELECT) |
| `ServerDetail` | 서버 상세 (full row) |
| `StorageWithUsage` | inventory disks/mounts + 시계열 mount_usage |
| `NetworkWithIo` | inventory IP + 시계열 net_io |
| `CollectionStatus` | last_metric_at + last_inventory_at |

### 메트릭 raw (delta 계산용)

| DTO | 용도 |
|-----|------|
| `DashboardRaw` | 4개 raw DTO 컨테이너 |
| `MetricPairRaw` | server_metrics 단일 행 (jiffies/KB/load + boot_time/agent_started_at) |
| `DiskIoRaw` / `NetIoRaw` | per dimension 단일 행 (누적 카운터 + boot_time/agent_started_at) |
| `MountUsageRaw` | per mount 단일 행 (시점값 + boot_time/agent_started_at) |
| `MetricSeries` | 차트 시계열 단일 포인트 (`collected_at`/`value`/`dimension`) |

### 보고서·산출물 raw

| DTO | 용도 |
|-----|------|
| `ReportRowRaw` | 보고서 한 행 raw stats — service mapper(`to_report_row_item`)가 `ReportRowItem` ViewModel로 변환 |
| `RebootEvent` | server_inventory_history에서 boot_time/agent_started_at 변경 시점 (`kind`: reboot/restart) |
| `InventoryExportEntry` | 정제 inventory JSON 항목 — 벤더 중립 v1 스키마. ViewModel은 아니지만 fastapi 응답으로 직접 노출 |

## Inbound DTO 타입 정책

| DTO | 컬렉션 필드 | 형태 | 이유 |
|-----|----------|------|------|
| `ServerInventoryCreate` | `disks`/`mounts`/`services`/`listen_ports` | `list[dict]` | JSONB 컬럼 직렬화 |
| `ServerMetricCreate` | `disk_io`/`mounts`/`net_io` | `list[DiskIoEntry]` 등 nested dataclass | 시계열 4테이블 행 매핑 — 컴파일 타임 타입 보장 |
| `TaskCreate.params` | `dict \| None` | JSONB | task_type별 스키마가 다름 — 동적 |
