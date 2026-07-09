# wire v2 엔진 마이그레이션 계획 (ingest -> DB -> 집계 -> 분류 -> 표시)

성격: 내부 구현 계획(삭제 자유). 엔진 코드 경로 명시. 근거 = 5계층 분석 종합.
전제: 프로덕션 없음 -> clean-cut 허용(구 스키마/데이터 폐기, 무손실 backfill 불요). 컨벤션·정석이 현행 프로젝트 원칙보다 우세. 우선 = ingest -> DB 저장(Tier 1~2).

정본 계약 = `docs/reference/contracts/agent-data.md` + `wire.schema.v2.json` + `v2-example-messages.json`.

## 0. 설계 결정 (확정)

1. 저장 모델 = 하이브리드. 스칼라 host 신호(memory.commit·hardware_corrupted·cpu.mce·conntrack·tcp_retrans·oom)는 wide `server_metrics` 컬럼(A). 가변 차원 신호는 전용 테이블(B) — `server_pressure`(PSI resource x scope x window), `server_disk_errors`(kind x class x member). 일반 metric-name+attr tall 테이블(C)은 counter_agg cagg 파이프라인·report_aggregate 컬럼 SQL 과 정면 충돌이라 불채택.
2. 단위 = clean-cut. jiffies->s(Float)·kB->By(BigInteger)·sectors->By·%->ratio. 구 컬럼/단위 drop, canonical 신컬럼 add. 과거 시계열 데이터 폐기(무손실 변환 불가·프로덕션 없음).
3. device 자연키 = 이름 -> 안정 id 문자열. `server_disk_io.device` -> `device_id`, `server_net_io.interface` -> `iface_id`, 표시명은 별도 컬럼(nullable). 자연키 UNIQUE(#C1) + `on_conflict_do_nothing(index_elements)`(#D2) 동시 재설계.
4. schema_version 라우팅 = flag-day(pre-prod). v1(flat) 파싱 폐기, 엔진은 v2(datapoint-array)만 소비. `schema_version=="2.0"` 검증, 불일치는 DLQ. `extra=ignore` 는 v2 내부 forward-compat 로 유지.
5. inventory block_devices/net_interfaces/lvm_vgs = JSONB pass-through(Tier 1 저장 모델). 단일행 upsert(agent_id UNIQUE) + history 미러 + 멱등성 모델 유지. 스토리지 3계층 쿼리는 mapper(P2) 파생. 정규화 자식 테이블(server_block_devices)은 3계층 SQL 쿼리 요구가 실제 확정될 때만(에스컬레이션).
6. cpu.time attr{cpu,state} = 엔진이 cpu 차원 합산해 host CPU 도출 + per-core 보존(`server_cpu_core` 유지, seconds 단위). host-wide/per-core wire 단일화를 엔진이 두 저장으로 분해.
7. PSI 저장 = `server_pressure`(server_id, resource, scope, window, collected_at) — ratio(gauge) + stall_time(counter s). 14일 saturation canonical = stall_time delta.

## 1. Tier 1 — ingest 기반 (저위험, 저장 체인 선행)

envelope + flat body(task.result/error). datapoint-array 무관, 선행 가능.

- `consumer/schemas.py` `MessageBase`: `schema_version: Literal["2.0"]` 신규 required. metrics envelope `hostname` optional 화(v2 metrics 는 hostname 없음, inventory·task.result 만). `os_family` 유지.
- `consumer/handlers/_common.py` `_log_time_invariants`(L99 `data.hostname`)·`build_placeholder_inventory`(data.hostname) -> hostname 부재 fallback(agent_id 기반 placeholder). metrics 경로에서 hostname 참조 제거.
- `consumer/main.py` dispatch: schema_version 검증(!="2.0" -> DLQ). routing key(broker) 와 message_type(body) 분리 유지.
- task.result: `install_verified` -> `task_policy`(bool|null) 개명 — `consumer/schemas.py` `TaskResultInput`·`consumer/handlers/task_result.py`(2곳)·`db/dtos/inbound.py` `TaskResultUpdate`·`tasks` 컬럼(`install_verified`->`task_policy`)·mapper. status Literal["success","failure"] -> free string(minLength 1) 완화. task_id 매칭.
- error: `schema_version` 만 추가(body 동일).

## 2. Tier 2 — DB 스키마 재설계 + 저장 (우선 — "저장하는 곳까지")

### 2a. inventory (JSONB pass-through)

- `db/models/server_inventory.py` + `server_inventory_history.py`(미러 lockstep):
  - `mem_total_kb` -> `mem_total_bytes BigInteger`. `swap_total_kb` 제거(swap 은 block_devices type=swap 노드).
  - `disks` + `mounts` JSONB -> `block_devices JSONB`(name/type/size_bytes/fstype/mountpoint/parent/id/id_type). `interfaces` JSONB -> `net_interfaces JSONB`(name/id/id_type/kind/speed_mbps/addresses[]/gateway). 신규 `lvm_vgs JSONB`.
  - `mac_addresses` -> net_interfaces id(mac) 파생(감사 목적 유지 논거 있으면 유지, 아니면 제거).
- `consumer/schemas.py` `InventoryInput` + nested(`BlockDeviceInfo`/`NetInterfaceInfo`/`NetAddressInfo`/`LvmVgInfo`/`ServiceInfo`/`ListenPortInfo`) v2 형태.
- `consumer/mappers.py` `to_inventory_create`·`build_placeholder_inventory` 신 컬럼셋.
- `db/dtos/inbound.py` `ServerInventoryCreate` 신 필드.
- `db/repositories/collect_repository.py` `_INVENTORY_COMPARE_COLS`·`_inventory_changed`·`_append_inventory_history` 3곳 lockstep + `upsert_server`.
- `web/services/device_filters.py` `kind` 판정 -> `type`/`id_type`, `find_parent_disk` major/minor -> parent id 체인. (web 소비처라 Tier 2 저장 후 배선 — 저장 스키마 확정 선행.)
- `service_classifier.compute_service_categories` 는 services/listen_ports 형태 유지라 무영향.

### 2b. timeseries (단위·자연키·신규 신호)

- `server_metrics` 재정의: cpu jiffies(BigInteger) -> `cpu_*_seconds Float`. `mem_*_kb` -> `mem_*_bytes`. 신규: `mem_commit_usage_bytes`/`mem_commit_limit_bytes`/`mem_hardware_corrupted_bytes`/`cpu_mce`(counter)/`memory_oom_kill`. conntrack/tcp_retrans 유지. `psi_*_some_total` 3컬럼 폐기(-> server_pressure). load_15m 폐기(vestigial). `collection_interval_sec` 유지.
- `server_disk_io` 재정의: `device`->`device_id`(안정 id) + `device_name`(표시). sectors/*_ms/reads_completed 폐기 -> `io_read_bytes`/`io_write_bytes`/`io_read_time_s`/`io_write_time_s`/`io_time_s`/`ops_read`/`ops_write`/`pending_ops`. kind 폐기.
- `server_net_io` 재정의: `interface`->`iface_id`(mac) + `iface_name`. bytes/packets/errors/drops 유지(단위 동일). 신규 `link_speed_bps`.
- `server_mount_usage` -> filesystem: `mount` + `device_id` 병기. used/free bytes(state) + inodes used/free.
- `server_cpu_core`: core_id 자연키 유지, jiffies -> seconds 8성분.
- 신규 hypertable: `server_pressure`(NK server_id,resource,scope,window,collected_at; ratio Float + stall_time_s Float counter; boot_time/agent_started_at). `server_disk_errors`(NK server_id,device_id,error_kind,error_class,member,collected_at; count).
- Alembic revision(수동, autogenerate 미지원 #C4): 컬럼 clean-cut(drop 구/add 신) + `create_hypertable` 보강 + 자연키 UNIQUE + boot_time/agent_started_at. 라운드트립 `alembic check`.
- `db/dtos/inbound.py` 저장 dataclass(`ServerMetricCreate`·`DiskIoEntry`·`NetIoEntry`·`MountUsageEntry`·`CpuCoreEntry` + 신규 `PressureEntry`·`DiskErrorEntry`) 신 필드.
- `collect_repository.record_metrics` + `_insert_*`(신규 `_insert_pressure`·`_insert_disk_errors`) on_conflict 자연키 동기화.

### 2c. v2 파싱 (datapoint-array -> 저장 DTO)

- `consumer/schemas.py` `MetricsInput` 전면 재작성: `system.cpu/memory/disk/network`(required) + `paging/filesystem/pressure/cgroup`(opt) 각 `{metric명: {type,unit,points:[{attr,value}]}} | null`. Pydantic 모델 `Namespace`/`Metric`/`Datapoint`.
- `consumer/mappers.py` `to_metric_create` 재작성: 네임스페이스 순회 -> metric별 points 순회 -> attr 로 차원 그룹핑(device_id·state·direction·resource·scope) -> 저장 DTO. cpu.time attr.cpu 합산(host) + per-core(server_cpu_core). null=미측정 보존(0 날조 금지, #B). `_max_disk_queue`/`_await_fields` 폐기(disk.operation_time 직접).
- `consumer/metric_normalize.py` clamp 불변식 재정의(available<=limit 등 By 단위).

## 3. Tier 3 — 집계 (cagg 재생성)

순서 고정(#C4): (a) 전 cagg + policy DROP -> (b) 컬럼/자연키 ALTER·신테이블(Tier 2) -> (c) cagg 재정의(`create_materialized_view` + policy, 트랜잭션 내) -> (d) `refresh_continuous_aggregate`(트랜잭션 밖 1회).
- `server_metrics_5m`: cpu seconds counter_agg(delta), mem bytes gauge, PSI stall_time(server_pressure)counter_agg rate, commit gauge.
- `server_disk_io_5m`: io_time counter_agg -> %util = delta(io_time)/time_delta. device 이름 GROUP BY -> device_id. `kind='physical'` 필터 -> type/id_type(inventory 조인 or metric device scheme).
- `server_net_io_5m`: interface -> iface_id GROUP BY. link_speed gauge.
- 신규 `server_pressure_5m`·`server_disk_errors_5m`(필요 시).
- `#C5` pruning(WHERE bucket>=) 유지. counter=counter_agg(hand-rolled LAG 금지).

## 4. Tier 4 — 분류 배선 (recommendation) — 상류 완료 후

- 4a: `ResourceStats` 신규 필드(psi_cpu/mem/io stall rate·disk_util_pct·mem_available_pct·commit_ratio) additive + `build_resource_stats`(report.py:366) 배선 + `report_aggregate` SQL 신 CTE/컬럼 + `ReportRowRaw`. non-breaking(default None).
- 4b: os-aware helper 3종(`cpu_saturated`·`mem_saturated`·`disk_io_saturated`) os_family 분기 -> PSI-first + source-attr 폴백 체인(PSI 없으면 procs_running/swap/await 유지 — 구 신호 폴백 게이트 필수). 임계 카탈로그(`docs/reference/right-sizing.md`·`_thresholds_reference.html`) PSI 근거로.
- 4c: disk %util U축 신설(`assess_disk_io` 확장·신규 trigger 키) + memory available 분모 전환(1-available/limit) — 분류 shift, cagg 재정의(Tier 3)와 동일 릴리스 필수. #E3 build_resource_stats 1곳 전파.
- 4d: 표시 동기화 `saturation_axis_displays`·`_build_saturation_axes`·`build_host_confidence_notes`·`_CAUSE_LABEL_BY_TRIGGER`·mapper·템플릿(#F9 fanout).

## 5. Tier 5 — 표시 (신규 신호 발화)

PSI·disk %util·errors·commit·conntrack 발화(#E9). ViewModel·템플릿 신규 축. 대개 mapper 파생이라 상류 완료 후.

## 6. Cross-cutting 의무

- #C1: 시계열 자연키 UNIQUE 재설계(device_id·PSI resource/scope) — 누락 시 #D2 멱등성 붕괴. 모델 변경 시 검증.
- #D2: `on_conflict_do_nothing(index_elements)` 인자를 신 자연키와 동기(`_insert_*`).
- #C4: 신 hypertable `create_hypertable` 수동 보강 + cagg 순서 + 라운드트립.
- #F9: 시계열 컬럼 (1)~(7) 체인 + inventory 컬럼 + agent-data.md(완료) 동기.
- boot_time/agent_started_at: 신 시계열 4테이블(+server_pressure counter) 공통 메타 보존(counter reset).

## 7. 순서 의존 요약

agent wire(락 완료) -> [T1 envelope/task] -> [T2 저장: inbound DTO + 모델/Alembic + 파싱 + repo] -> [T3 cagg 재생성] -> [T4 report_aggregate SQL -> ReportRowRaw -> build_resource_stats -> recommendation] -> [T5 표시]. T1~T2 = "저장하는 곳까지"(우선 자율 진행). T3 이후는 상류 land 후.

## 8. 진행 상태 (2026-07-09 자율 진행)

완료·검증 (ingest 파싱 = wire -> 저장 DTO, 예시 6종 fixture 단위검증):
- `consumer/schemas.py` v2 — MessageBase(schema_version)·MetricsInput(system.* datapoint-array: Namespace/Metric/Datapoint)·InventoryInput(block_devices/net_interfaces/lvm_vgs/정적 서술자/mem_total_bytes)·TaskResultInput(task_policy·free-string task_id)·ErrorInput. `tests/unit/test_v2_wire_schemas.py` 10 pass.
- `db/dtos/inbound.py` v2 저장 DTO — ServerInventoryCreate(block_devices/net_interfaces/lvm_vgs JSONB·mem_total_bytes)·ServerMetricCreate(cpu_*_s seconds·mem_*_bytes·commit·paging·conntrack·per-device nested)·DiskIoEntry(device_id·io_time_s·op_*_time_s)·NetIoEntry(iface_id·link_speed_bps)·FilesystemEntry·CpuCoreEntry(seconds)·PressureEntry(resource/scope·stall_time_s·ratio_avg*)·DiskErrorEntry(가변 차원). TaskResultUpdate(task_policy).
- `consumer/mappers.py` v2 — datapoint-array 순회 + attr 매칭 헬퍼(_match·_scalar·_state_sum·_distinct). CPU host=cpu.time attr.cpu 합산 + per-core 병행. disk/net per-device·filesystem per-mount·pressure(resource x scope, window 평탄화)·disk_errors(가변 kind/class). null=미측정 보존. `tests/unit/test_v2_mappers.py` 11 pass(값 정합·Windows pressure null·placeholder).

완료·검증 (DB 쓰기 절반 — fresh v2 TimescaleDB 실검증, timescaledb-ha:pg16):
- `db/models/*.py` — 시계열 s(Float)/By(BigInteger) 컬럼·device 안정 id 자연키(device_id·iface_id)·server_filesystem(개명)·server_pressure(NK resource/scope)·server_disk_error(NK device_id/kind/class/member)·server_metrics 컬럼 교체·server_inventory JSONB(block_devices/net_interfaces/lvm_vgs·mem_total_bytes). envelope 메타(boot_time/agent_started_at)는 server_metrics 에만 — 자식 시계열 미보유(cpu_core 규약 확장, counter_agg reset-safe). 11 테이블 Base.metadata 등록 확인.
- Alembic 단일 baseline `53df4c2132fd_v2_wire_schema_baseline.py` — v1 38 revision squash(flag-day, 프로덕션 없음). autogenerate + create_hypertable(8 시계열) 수동 보강. `alembic check` drift 0 · downgrade base->upgrade head 라운드트립 · 8 hypertable 등록 확인.
- `collect_repository.py` v2 — record_metrics(_insert_child 제네릭 + _insert_disk_error member 정규화) on_conflict 신 자연키(#D2). upsert_server + `_inventory_row` 공용·_INVENTORY_COMPARE_COLS·history 미러 신 컬럼셋. `install_verified`->`task_policy`(Task 컬럼·complete_task).
- `consumer/handlers/*.py`·`_common.py` — v2 metrics hostname 부재 대응(placeholder=agent_id, time_invariant 쿨다운키 agent_id 단일, agent_started_at None 가드), task_result.py `task_policy`. `task_policy.py` 파라미터 개명 + 회고형 언어 현황화. `metric_normalize.py`(clamp_ceiling) 제거 — v2 는 used 를 wire state=used 직접 판독이라 "used 음수 방지" 근거 소멸(dead code).
- end-to-end 스모크(mapper->repo->실 DB): upsert 멱등(history 중복 0)·record_metrics 전 자식 적재(pressure/disk_error 포함)·재삽입 전부 0(자연키 멱등)·windows placeholder auto-register·값 정합(cpu_idle_s seconds·mem By·paging).
- `tests/unit/test_v2_*` 21 pass 유지.

doc-tier 잔여(wrap-up/T5 에서): squash 로 `docs/explanation/tradeoffs.md:305` 의 삭제된 마이그레이션 파일경로 포인터가 깨짐(ADR 0042/0049·tradeoffs 의 revision-id 언급은 불변 아카이브라 사료로 유효). `docs/guides/migrate.md` 등 마이그레이션 체인 서술 v2 현황화 필요.

미완 (read/집계/표시 — web import 는 query/*.py 가 아직 v1 이라 깨짐, 예정된 단계):
- T3 cagg 재생성 — server_*_5m continuous aggregate 를 v2 컬럼(s/By·counter_agg)·신 자연키로 재정의. timescaledb_toolkit extension.
- T4 recommendation — PSI stall_time 우선 saturation·paging os-aware·신 컬럼 배선.
- T5 표시 — query repositories·services·mappers·templates·JS 를 v2 스키마로 (server_mount_usage->server_filesystem·mem_total_kb->mem_total_bytes·sat_*->cpu_run_queue/pressure 등). web import 복구는 이 단계 완료 시.

브랜치/커밋: refactor/layer-audit 미커밋(구 커밋 e3b22a4 clean). 엔진 compose down.
