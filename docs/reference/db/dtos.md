# DB DTO 카탈로그

정책: CLAUDE.md #C2. Repository·Service 경계 dataclass — ORM 모델 직접 노출 금지.

## Inbound DTO (`inbound.py`) — Service → Repository

| DTO | 용도 | 필드 정책 |
|-----|------|-----------|
| `ServerInventoryCreate` | inventory upsert | JSONB 컬럼(`disks`/`mounts`/`services`/`listen_ports`)은 `list[dict]` — JSONB 직렬화에 자연 |
| `ServerMetricCreate` | metrics 1건 | nested `list[DiskIoEntry]`/`list[NetIoEntry]`/`list[FilesystemEntry]`/`list[CpuCoreEntry]`/`list[PressureEntry]`/`list[DiskErrorEntry]` — 시계열 metric 7테이블 행 매핑이라 컴파일 타임 타입 보장. boot_time/agent_started_at 은 server_metrics 만 |
| `DiskIoEntry` / `NetIoEntry` / `FilesystemEntry` / `CpuCoreEntry` / `PressureEntry` / `DiskErrorEntry` | 시계열 행 nested | dict 키 오타 방지 — mapper 단계에서 차단. INSERT 시 `vars(entry)` shallow spread로 풀어쓰기 |
| `TaskCreate` | task 발행 | target_server_id / target_agent_id / task_type / params (JSONB) |
| `TaskResultUpdate` | task 결과 수신 | public_id / status / failure_reason / exit_code / signal_no / duration_ms / stdout_tail / stderr_tail / completed_at |

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
| `SaturationRaw` | server별 실시간 포화 원자료 (run_queue·await·큐·paging·retrans). 미존재 server 는 빈 인스턴스(전 필드 None) sentinel |

### 보고서·산출물 raw

| DTO | 용도 |
|-----|------|
| `ReportRowRaw` | 보고서 한 행 raw stats — service mapper(`to_report_row_item`)가 `ReportRowItem` ViewModel로 변환 |
| `DiskIoBaselineRaw` / `NetIoBaselineRaw` | server별 I/O baseline + p95/peak (`report_disk_io_baseline`/`report_net_io_baseline` 반환, service 가 raw 에 필드 대입) |
| `RebootEvent` | server_inventory_history에서 boot_time/agent_started_at 변경 시점 (`kind`: reboot/restart) |
| `MountCapacityRaw` | 마운트별 용량 사이징 입력 (`report_mount_capacity_batch` 반환) — total/target bytes·runway·used%·inode. assessment API disk 축(per-mount) 산출 |

## Inbound DTO 타입 정책

| DTO | 컬렉션 필드 | 형태 | 이유 |
|-----|----------|------|------|
| `ServerInventoryCreate` | `disks`/`mounts`/`services`/`listen_ports` | `list[dict]` | JSONB 컬럼 직렬화 |
| `ServerMetricCreate` | `disk_io`/`net_io`/`filesystems`/`cpu_per_core`/`pressure`/`disk_errors` | `list[DiskIoEntry]` 등 nested dataclass | 시계열 metric 7테이블 행 매핑 — 컴파일 타임 타입 보장 |
| `TaskCreate.params` | `dict \| None` | JSONB | task_type별 스키마가 다름 — 동적 |
