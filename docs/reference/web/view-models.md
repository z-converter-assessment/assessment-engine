# Web ViewModel 카탈로그

정책: CLAUDE.md #E3 (mapper 단일 변환) · #E8 (차트·도넛 UI). 본 문서는 ViewModel 카탈로그·신호 임계값 단일 정의. 신규 파생 필드 추가 시 `cache_serializer._DETAIL_DISPLAY_FIELDS` 동기화 필수.

## 서버 표시

| ViewModel | 채우는 mapper | 핵심 파생 |
|-----------|---------------|-----------|
| `ServerListItem` | `to_server_list_item` | `os_display` / `mem_total_gb` / `storage_total_gb` / `is_online` / `known_services` (카테고리 dedup) / `show_unknown_badge` / `recommendation_label`(한국어 분류명 `recommendation.LABEL_KO`) / `provisioning_class`(raw enum — 목록 색은 이 필드 기반 under-only 강조, 분류 다색 배지는 상세/보고서 전용) / `os_eol_status`(지원 단계 — full·security_only·paid_only·ended·unknown, 카탈로그 미매칭을 "지원 중"으로 단정 안 함) / `has_operational_event`(전기간 에러 발생 유무, `fleet_error_hosts` 집합) |
| `ServerDetailResponse` | `to_server_detail` + `enrich_server_detail` | `os_display`(Windows 는 `product_name` 연도/세대 라벨 우선 — `_os_display` 4단 폴백) / `edition`(Windows SKU pass-through, `detail.html` os_display 조합 표시) / `cpu_display` / `disk_total_gb` / `services` (ServiceItem) / `sorted_listen_ports` / `agent_id`(식별 단일 키 표시) / `cpu_arch`+`cpu_bits`(ISA·비트, pass-through) |
| `ServiceItem` | mapper | `category` (`service_classifier.classify`) / `matched_ports` (port 리스트) / `display_name` |
| `ListenPortItem` | mapper | `is_significant` (boolean, port < 49152 = 비동적 포트) — 매퍼가 `key_listen_ports` 를 고를 때 쓴다 |
| `MountUsageItem` | `_build_mount_item` | `mount` / `fstype` / `total_gb`·`used_gb`·`avail_gb` / `usage_pct` / `badge_class`·`bar_color` (임계값 분류) |

## 메트릭 대시보드

| ViewModel | 채우는 함수 |
|-----------|-------------|
| `MetricDashboard` | `build_dashboard`(활용률 스냅샷) + `build_saturation_signals`(자원별 포화 신호 4리스트) + `build_error_signals`(에러 fleet). 개요·자원 탭 스냅샷 카드 공용 |
| `CpuSnapshot` | jiffies delta 기반 `usage_pct`/`user_pct`/`system_pct`/`iowait_pct`. boot_time reset 시 None |
| `MemSnapshot` | 시점값 + stacked bar 누적 비율 (`cached_pct`/`buffers_pct` 100% 초과 방지 clip) |
| `DiskIoSnapshot` / `NetIoSnapshot` | rate (`d_val / dt`). reset 시 None |
| `SaturationSignal` | os-aware 포화 스냅샷 1개 — `label`/`value`/`threshold`/`unit`/`saturated`/`state`(4상태: measured·no_data·not_applicable·insufficient)/`detail`(hover). 판정은 도메인 os-aware helper 경유(임계 재계산 금지, #E3). 클라 `SignalUtils.renderSaturation` 렌더만 |
| `ErrorSignal` | 에러 축 표시자 1개(카운트형, 정상=0 발화 #E9) — `label`/`state`(4상태: clean·occurred·no_data·not_applicable — no_data 는 일시 미수집, not_applicable 은 이 OS 구조적 미지원)/`count`/`context`(종류)/`window_label`. `SignalUtils.renderErrors` 렌더 |
| `CpuCoreSnapshot` | `build_dashboard` | 코어별 순간 `usage_pct` — 단일스레드 병목 실시간(Linux 전용, Windows 빈 list). CPU 상세 전용 축 |

`MetricDashboard` 추가 필드(개요·자원 탭 공용 스냅샷 카드): `disk_io`/`net_io` 는 물리 디바이스만(`device_filters` 단일 진실, LV·파티션·가상 인터페이스는 이중집계 제외) · `disk_usage_pct`(데이터 볼륨 파일시스템 used/total 집계 %, 실시간 카드 도넛) · `cpu_cores`(위 표).

## 자원 상세 탭 '최근 N일' 평가 카드 (CPU/메모리/스토리지/네트워크, 평가 윈도우 창)

서버 세부(`/servers/{id}`) 및 4개 자원 상세 탭(`/cpu`·`/memory`·`/storage`·`/network`)이 공유하는 이용률(U)+포화(S)+에러(E) 3축 카드 — `build_period_assessment(stats, errors, disk_worst_mount=)`(mappers/report.py) 단일 산출, `query/server.py.get_period_assessment` 경유 각 라우터가 조회(`server_detail.py`). 실시간 스냅샷 카드(순간)와 분리 — 이쪽은 `recommendation.WINDOW_DAYS`(#F10) 통계 기준의 분류·판정 근거.

| ViewModel | 핵심 필드 |
|-----------|-----------|
| `PeriodAssessment` | `resources`([cpu, mem, disk, net] 순 `PeriodResource` 4개) / `error_rows`(전 자원 통합 `PeriodErrorRow`) / `window_days` / `classification_label`+`classification_color`(종합 배지 — `classify_host` 과 동일 단일 진실, 목록-세부 정합) |
| `PeriodResource` | `util_rows`/`sat_rows`(`PeriodSignalRow` 리스트, over 개수 = `util_over`/`sat_over`) / `has_util`(네트워크만 False — 처리량 축이라 용량% 없음) / `detail_slug` / `verdict_label`+`verdict_color`(자원별 판정, `rollup_host` 재사용) / `extra_groups`(자원별 상세 탭 전용 "신뢰도" 카드, `PeriodExtraGroup`) / `error_rows`(메모리만 채움 — `mem_` 접두 필터) / `verdict_label2`+`verdict_color2`(스토리지 전용 — 용량 축과 독립된 성능/IO 축 2번째 배지, 나머지 자원은 빈 문자열) |
| `PeriodSignalRow` | `label`/`value`/`threshold`(형식화 문자열, 템플릿 계산 0)/`over`(임계 이상)/`measured`(False면 N/A muted) |
| `PeriodExtraGroup` | `label`("부하 신호"/"통계 신뢰도" 등) + `rows`(`PeriodSignalRow`) — 성격별 그룹 |
| `PeriodErrorRow` | `key`(`mem_oom` 등 — 자원별 탭이 자기 자원 접두만 필터)/`label`/`badge_text`+`badge_class`/`note`/`sizing_signal`(OOM 발생 시 "메모리 자원 부족", 그 외 "") |

스토리지 "사용률" 행만 다른 자원(호스트 p95 집계)과 다른 산식 — worst-mount(가장 채워진 마운트 1개, `disk_worst_mount` 파라미터로 라벨에 마운트명 병기) — 실시간 카드 도넛(`disk_usage_pct`, 전체 마운트 가중평균)과 의도적으로 다른 값(#F9 명시 표기).

단일 서버 보고서(`/servers/{id}/report?view=engineer`)도 동일 `build_period_assessment` 를 호출해 `EnvironmentReportSummary.period_assessment` 에 정적 스냅샷으로 저장 — CPU/메모리/스토리지/네트워크 상세 카드가 이용률·포화 2열로 노출(라이브 조회 아닌 발행 시점 고정, #C1).

## 스토리지 레이아웃 트리 / 네트워크 인터페이스 정보

| ViewModel | 채우는 mapper | 핵심 파생 |
|-----------|---------------|-----------|
| `StorageNode` | `build_storage_tree(block_devices, lvm_vgs, filesystems)` | 물리 디스크 루트 트리 — `kind`(block_device type 또는 파생 `unallocated`/`vg_free`)+`kind_label` / 계층별 자기 속성만(`meta`) / 마운트 노드만 사용량 2축(`usage_pct`+`usage_label`+`usage_class`, `inode_pct`+`inode_label`+`inode_class`) / `gauge_info_width_px`(깊이 들여쓰기 상쇄, 게이지 x좌표 통일). 다중 부모(RAID span·striped VG)는 디스크별 그룹으로 반복 노출(DAG, 순수 트리 불가) |
| `NetworkInterfaceInfo` | `build_network_interfaces` | 물리 인터페이스만(`device_filters.is_virtual_interface`) 정적 구성 — `mac`/`mtu`/`speed_mbps`/`gateway`/`dns`/`addresses`(`NetIfaceAddress` — CIDR+`is_ipv4`+`origin`). 활동(RX/TX/pps)은 `NetIoSnapshot` 별개 축(실시간 카드) |

`StorageDetailResponse.tree`/`NetworkDetailResponse.interfaces_info` 가 각각 소비. 둘 다 `os_family`(N/A 표시 OS 분기, #E6 `data-os-family`) 동반.

단일 서버 보고서(engineer)도 동일 `build_storage_tree`/`build_network_interfaces` 를 호출해 `EnvironmentReportSummary.storage_tree`/`network_interfaces` 에 정적 스냅샷으로 저장(발행 시점 1회 조회, `_storage_tree.html` `storage_tree()` 매크로 재사용) — 스토리지 상세 카드가 마운트별 표 대신 이 트리를 노출.

## 보고서·산출물

| ViewModel | 채우는 mapper |
|-----------|---------------|
| `ReportRowItem` | `to_report_row_item(raw, online, now, has_operational_event=False)` — `role`(`infer_role`, listen 보강) / `recommendation`(`classify_host`) / `recommendation_label` (한국어) / `badge_class` (`rec-{enum}`) / `root_cause_label`(`rollup_host` 인과 종합) / `net_status_label`(네트워크 품질 정상·혼잡·미측정, 사이징과 별개) / `os_display` / `internal_ip[0]`. 특징 워크로드(baseline 제외): `workload_categories`(카테고리 집합) / `signature_workload_categories`(`SIGNATURE_CATEGORIES` 부분집합 — 세부 서버 목록 "구동 서비스" 열 전용, 서버 목록 뱃지와 동일 기준) / `workload_services`(카테고리별 서비스명) — 환경/N대 집계·세부 목록 뱃지 공유. 구동 서비스 차등(개별 보고서, `_build_workload_display`, baseline 포함 전부): `workload_groups`(customer 카테고리별 제품명) / `listen_ports_detail`(engineer Listen 포트 카드 — listen 소켓 원시 표). `os_eol`/`os_eol_status`(`lookup_os_eol(...,  now.date())` full·security_only·paid_only·ended·unknown — 보고서 발행 기준 시각, live "오늘" 아님) / `has_operational_event`(보고서 window 기준 `latest_errors` — 세부 서버 목록 전용, caller 가 `get_report(fetch_operational_events=True)` 로 명시 요청할 때만 계산, 기본 False — 환경 전체 스코프는 N+1 회피로 미계산) |
| `ReportSummary` | `query_service.get_report` — `rows: list[ReportRowItem]`(`sort_rows_for_report` 위험 우선 정렬) + KPI 집계 (`total`/`online`/`risk_attention`(과다+유휴)/`risk_high`(부족)) + N대 선택 맥락 `os_family_summary`/`workload_summary`(`build_selection_context`) |
| `MetricSeriesItem` | `to_metric_series_item` — chart API 응답 |

## 환경 개요 상단 요약 (overview, `/`)

환경 개요(`/`)는 `EnvironmentOverview`(환경 요약 KPI + 활용률·포화 도넛 + 자원 적정성 분포 막대)만 노출 — 서버 목록(`/servers`)은 행만, 화면 분리 자체가 컨텍스트 가드(#E9). `AttentionSignals`(운영신호 3 카탈로그 — 통신끊김/OS지원종료/에이전트재시작, `get_attention_signals`)는 보고서 경로에서만 소비 — 단일/선택은 `attention_for_host`/`attention_by_host`(`services/report_generator.py` 정의, `report_page.py` 소비), 환경·선택 요약은 `EnvironmentReportSummary.attention`(OS 지원종료 표). 독립 라이브 카드로는 렌더되지 않는다.

요약 위젯·right-sizing 분류 모두 `recommendation.WINDOW_DAYS`(#F10) — 한 창 통일(#E3 화면 간 정합).

| ViewModel | 채우는 mapper | 데이터 소스 | 시간 축 | 색상 톤 |
|-----------|---------------|-------------|---------|---------|
| `EnvironmentOverview` | `build_environment_overview` — 화면 축 6: 환경 요약 KPI(대수·온라인/오프라인·자원 합계·`os_distribution`·OS 지원 4분기 `os_eol_passed`(무상 패치 종료 — paid_only·ended 합산)/`os_eol_security_only`/`os_eol_unknown`/`os_eol_supported`) / 주요 워크로드(`role_distribution` 시그니처 카테고리 인스턴스 분포 — `SIGNATURE_CATEGORIES`, 호스트 dedup 아님·0 포함 + `workload_donut`·`workload_total`·`role_unknown_count`) / 활용률(`utilization` 평균 + `utilization_p95` 환경 보고서 소비, 표본 `util_sample_size`) / 포화 4도넛(`saturation_donuts` — CPU 포화·메모리 압박·디스크 I/O 포화·네트워크 혼잡) / 에러 fleet(`error_fleet` — MCE·OOM·EDAC·디스크·NIC 에러 발생 호스트 수) / 분류 도넛(`risk_donut`·`risk_donut_total`·`risk_high_count`) | `list_server_ids` + `get_servers` + `environment_utilization` + `report_aggregate` + `fleet_error_summary` + Redis `online:*` mget | 자원 적정성 창 (`WINDOW_DAYS`, #F10) | slate (`#f8fafc`) |
| `FleetErrorItem` | 환경 fleet 에러 표시자 1개 — `label`/`affected`(발생 호스트 수)/`total`(표본). 정상=0 발화(#E9), 카운트형이라 도넛 아닌 표시자. `_build_error_fleet` | `fleet_error_summary` | 위 창 | — |
| `UtilizationBar` | `build_environment_overview` 안에서 3종 (CPU·메모리·디스크) 생성 — `pct`/`bar_color`(단색 푸른, 값 무관)/`dash_length`(SVG dasharray, mapper 비례 산술) | `environment_utilization(WINDOW_DAYS, end)` SQL — CPU·메모리·디스크 모두 capacity-weighted (Σused/Σtotal, 자원 총량 가중 — 서버 1대=1표 아님) | 평가 윈도우 | 테마색1 `var(--color-title)`·`None` 회(`#cbd5e1`) |
| `RiskDonutSegment` | `build_risk_donut_segments` — 5 카테고리 (under/over/idle/optimal/insufficient) `key`/`label`/`color`/`count`/`dash_length`/`dash_offset` (multi-segment 누적 음수) | `report_aggregate(WINDOW_DAYS)` + net baseline 주입 -> `build_resource_stats` -> `classify_host` -> `_DONUT_SEGMENT_FROM_REC` | 평가 윈도우 USE Method | `UTIL_GAUGE_COLOR` 테마 단색 (분류는 라벨이 전달, E8) |
| `AttentionRow` (gap) | `to_gap_warning_item(raw, now)` — `badge_text`(경과 분 `{gap_min}분`) / `badge_class`(`attn-active`, 운영신호 통신끊김) | `metric_gap_warnings(gap_min, recent_h, limit)` 단일 SQL | 5min~24h 갭 | blue (`#eff6ff`) |
| `CapacityWarningItem` | `to_capacity_warning_item(raw)` — `active_causes`(발화 원인 os-neutral 라벨, `_CAUSE_LABEL_BY_TRIGGER` 파생 — 환경 요약 원인 집계 `_under_cause_summary` 단일 소스)·`recommendation_action`(자원별 독립 처방, `under_prescription` — 인과 결합이어도 관측된 under 자원 전부)·`root_cause_label`(진단 근거 전용, 처방을 거르지 않음)·`net_status_label`+`net_status_color`(네트워크 품질 전용 필드 — `action_targets_table` 전용)·`disk_io_status_label`+`disk_io_status_color`(디스크 I/O 상태, network 와 동형)·`spec_display`(정적 배정 사양 "4코어 · 8.00GB · 100GB", `shared.spec_display_line` 단일 진실 — `ServerListItem.spec_display` 와 동일 산식, 표 호스트 옆 노출). caller가 `under_provisioned` 필터링 -> EnvironmentOverview.under_provisioned_hosts (운영신호 아님, USE Method) | `report_aggregate(WINDOW_DAYS)` + `build_resource_stats` -> `rollup_host`(triggers) | 평가 윈도우 USE Method | blue (`#eff6ff`) |
| `AttentionRow` (os_eol) | `to_os_eol_warning_item(raw, now)` — `resolve_os_eol`(endoflife 카탈로그) 무상 보안 패치 종료 시 반환 (운영신호) | `os_id`/`os_version`/`kernel_version` + endoflife 스냅샷 카탈로그 | endoflife.date 스냅샷 (Linux distro + Windows Server build) | blue (`#eff6ff`) |
| `AttentionRow` (agent_unstable) | `to_agent_unstable_item(public_id, hostname, restart_count)` — caller가 임계 필터링 | `agent_restart_counts_recent(since=now-1h)` SQL (`server_inventory_history` `agent_started_at` DISTINCT-1) | 1h fixed 윈도우 (Redis sliding 대체) | blue (`#eff6ff`) |
| `AttentionSignals` | `query_service.get_attention_signals` 묶음 (내부 `_assemble_attention` 조립) — 운영신호 3 카탈로그(gap·os_eol·agent_unstable). `has_any` property로 빈 카드 분기 | 위 3 builder(gap/os_eol/agent_unstable) | — | blue (`#eff6ff`) |

신호 임계값 단일 정의 (mapper·service 모듈 상단):
- `_USAGE_DANGER_PCT = 90` — disk_warning 공통 (mapper)
- `_USAGE_WARN_PCT   = 75` — 위험도 분류 보조
- `_UTIL_DONUT_CIRC` — `2*pi*_DONUT_RADIUS`(템플릿 SVG r 과 정합) 단일 진실 (mapper, E8)
- `_DONUT_SEGMENT_FROM_REC` / `_DONUT_SEGMENT_DEFS` — 자원 적정성 5 카테고리(키·라벨·색) 단일 매핑
- `_CAUSE_LABEL_BY_TRIGGER` — trigger key -> os-neutral 원인 라벨 (자원 부족 원인 집계 단일 진실, mapper)
- `agent_restart_alert_threshold` — 1h 윈도우 재시작 임계 (WebSettings, 값은 `docs/reference/contracts/env.md`)
- 디스크 용량·I/O 임계 — 본 목록 아님(운영신호가 아닌 USE Method 분류 축, `recommendation` 모듈 단일 진실)
- `_eol_info` (mapper, shared) — endoflife.date 스냅샷 카탈로그(`os_eol_catalog.json`) 조회 + 경계 3개로 지원 단계 판정 단일 진실.

  경계는 무엇이 끊기는지로 정의한다 — `support` 기능 업데이트, `eol` 무상 보안 패치, `extendedSupport` 유상 보안 패치. 벤더 용어(Windows Mainstream·Extended·ESU / RHEL Full·Maintenance·ELS)는 쓰지 않는다. Microsoft 의 "Extended Support" 가 여기서는 `security_only` 구간이라 카탈로그 필드명과 반대이기 때문이다.

  | 상태 | 조건 |
  |------|------|
  | `full` | support 미도래 (또는 support 미수록 + eol 미도래) |
  | `security_only` | support 경과 + eol 미도래 |
  | `paid_only` | eol 경과 + extendedSupport 미도래 |
  | `ended` | extendedSupport 경과, 또는 eol 경과인데 유상 경로 부재 |

  카탈로그가 경계를 다 싣지는 않는다 — 어느 경계가 없으면 그 구간이 존재하지 않는다는 뜻이다. fedora·opensuse 는 유상 연장이 없어 eol 경과가 곧 `ended` 이고, debian·sles 는 `support` 가 없어 `security_only` 구간이 없다.

  매칭은 Linux 가 `os_id`->product slug(`_OS_ID_TO_EOL_PRODUCT`) + `os_version`->cycle, Windows 가 `kernel build`->windows-server latest build (운영=Server 가정). 빌드가 복수 채널에 겹치면 후보 전체를 판정한 뒤 심각도 최소를 택한다 — 불확실할 때 과소지원으로 오판하지 않는 쪽.

  래퍼 둘. `resolve_os_eol`(발화용 — `paid_only`·`ended` 만 반환, attention 카드·보고서 요약. 유상 계약 여부는 수집할 수 없어 계약이 없다는 쪽으로 본다) / `lookup_os_eol`(표시용 — `OsEolInfo(eol_iso·support_iso·extended_support_iso·label·status)` 반환, 서버 목록·상세·보고서 상태 칼럼).

활용률 게이지 색 카탈로그 (mapper 상수):
- `UTIL_GAUGE_COLOR = "var(--color-title)"` (shared 단일 진실, attention `_UTIL_COLOR_GAUGE` alias) — 주색(테마색1). 활용률 정도는 게이지 길이(`dash_length`)로, 색은 값 무관 단일. Right-sizing 과다프로비저닝(`_DONUT_SEGMENT_DEFS` over)·서버목록 `.rec-over_provisioned` 배지가 동일 주색 공유 (테마 통일, static-assets.md "색 테마"). under(`#ef4444`)와 대비.
- `_UTIL_COLOR_NONE = "#cbd5e1"` — 표본 부재 (회색).

