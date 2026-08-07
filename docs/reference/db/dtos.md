# DB DTO 카탈로그

정책: CLAUDE.md #C2. Repository·Service 경계 dataclass — ORM 모델 직접 노출 금지.

## Inbound DTO (`inbound.py`) — Service → Repository

| DTO | 용도 | 컬렉션 필드 정책 |
|-----|------|-----------------|
| `ServerInventoryCreate` | inventory upsert | 정규화 스토리지·네트워크 그래프는 `list[dict]`·`dict` pass-through — JSONB 컬럼 직렬화 |
| `ServerMetricCreate` | metrics 1건 | 시계열 nested 는 entry dataclass 리스트 — 시계열 metric 7테이블 행 매핑이라 컴파일 타임 타입 보장. `boot_time`·`agent_started_at` 은 `server_metrics` 만 |
| `DiskIoEntry` / `NetIoEntry` / `FilesystemEntry` / `CpuCoreEntry` / `PressureEntry` / `DiskErrorEntry` | 시계열 행 nested | dict 키 오타를 mapper 단계에서 차단. INSERT 는 `vars(entry)` shallow spread |
| `TaskCreate` | task 발행 | `params` 만 JSONB — task_type 별 스키마가 달라 동적 |
| `TaskResultUpdate` | task 결과 수신 | 종료 신호(`exit_code`·`signal_no`·`task_policy`) 의미는 CLAUDE.md #B |
| `DiagnosticJobCreate` | 보고서 발행 job INSERT | `id`·`created_at`·`status` 는 DB default |

## Outbound DTO (`outbound.py`) — Repository → Service

모두 raw 단위 (P1) — canonical 은 시간 s(Float)·크기 By(int) 그대로. 변환은 service.

전부 `frozen=True, slots=True` 다. 필드가 많은 raw 행(`ReportRowRaw`)에서 오타 대입이 조용히 새 속성을 만드는
것을 slots 가 런타임에, pyright 가 컴파일 시점에 막는다. 값을 얹을 때는 `dataclasses.replace` 로 새 행을
만든다 — 제자리 수정이 없으니 호출부가 결과를 다시 묶지 않으면 반영이 통째로 빠져 즉시 드러난다.

Inbound entry dataclass 는 이 규칙 밖이다 — `collect_sql` 의 `vars(entry)` shallow spread 가 `__dict__` 를
전제한다. slots 를 붙이면 인제스트가 첫 메트릭 메시지에서 죽는다.

### 서버 표시 raw

| DTO | 용도 |
|-----|------|
| `ServerSummary` | 서버 목록 (큰 JSONB·텍스트 제외 명시 SELECT) |
| `ServerDetail` | 서버 상세 (full row) |
| `StorageWithUsage` | inventory `block_devices`/`lvm_vgs` + 시계열 마운트 사용량 |
| `NetworkWithIo` | inventory IP + 시계열 net_io |
| `CollectionStatus` | last_metric_at + last_inventory_at |
| `TaskRow` | task 조회 raw (단건·타임라인·서버별 최신 공용) — 표시 파생(badge_class·duration_label)은 mapper |

### 메트릭 raw (delta 계산용)

| DTO | 용도 |
|-----|------|
| `DashboardRaw` | raw DTO 5리스트 컨테이너 (metrics·disk_io·net_io·filesystems·cpu_cores) + os-aware 판정 입력(os_family·kernel_version·block_devices·net_interfaces) |
| `MetricPairRaw` | server_metrics 단일 행 (CPU 초 counter / 메모리 By / run_queue·blocked gauge + boot_time·agent_started_at) |
| `CpuCoreRaw` | per-core CPU 시간 원자료 (Linux 전용 — 단일스레드 병목 실시간 표시) |
| `DiskIoRaw` / `NetIoRaw` | per dimension 단일 행 (누적 카운터 + boot_time/agent_started_at) |
| `MountUsageRaw` | per mount 최신 1행 (gauge 시점값) |
| `MetricSeries` | 차트 시계열 단일 포인트 |
| `SaturationRaw` | server별 실시간 포화 원자료. 미존재 server 는 빈 인스턴스(전 필드 None) sentinel |
| `ErrorFleetRaw` | 창내 하드웨어·디스크·네트워크 에러 카운트 (Errors 축, 정상 0). 표본 없음은 `measured=False` 로 no_data 구분 |
| `FleetErrorRaw` | 함대 에러축 영향 호스트 수 (환경 개요 표시자) |
| `EnvironmentUtilizationRaw` | 환경(또는 선택 N대) capacity-weighted 평균 활용률 (sum(used) / sum(total)) |

### 보고서·산출물 raw

| DTO | 용도 |
|-----|------|
| `ReportRowRaw` | 보고서 한 행 raw stats — service mapper(`to_report_row_item`)가 `ReportRowItem` ViewModel로 변환 |
| `DiskIoBaselineRaw` / `NetIoBaselineRaw` | server별 I/O baseline + p95/peak — service 가 baseline 을 합성해 새 raw 를 만든다 |
| `RebootEvent` | server_inventory_history에서 boot_time/agent_started_at 변경 시점 (`kind`: reboot/restart) |
| `MountCapacityRaw` | 마운트별 용량 사이징 입력 — assessment API disk 축(per-mount) 산출 |
| `MemoryBreakdownRaw` / `CpuBreakdownRaw` | 개별 보고서 구성 윈도우 평균 — 메모리 used/available/cached/buffers, CPU user/system/iowait |
| `MetricGapWarningRaw` | metric 발행 갭 운영신호 후보 |
| `DiagnosticJobRecord` | 보고서 발행 job 단건 — 라우터 조회 응답·발행 이력 표현. status 에 따라 `result` 또는 `error_message` 한쪽만 채움 |
