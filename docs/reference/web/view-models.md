# Web ViewModel 카탈로그

정책: CLAUDE.md #E3 (mapper 단일 변환) · #E8 (차트·도넛 UI). 신규 파생 필드 추가 시 `cache_serializer._DETAIL_DISPLAY_FIELDS` 동기화 필수.

## 서버 표시

| ViewModel | 채우는 mapper | 핵심 파생 |
|-----------|---------------|-----------|
| `ServerListItem` | `to_server_list_item` | `os_display` / `mem_total_gb` / `storage_total_gb` / `is_online` / `known_services` (카테고리 dedup) / `show_unknown_badge` / `recommendation_label`(한국어 분류명 `right_sizing.RECOMMENDATION_LABEL_KO`) / `provisioning_class`(raw enum — 목록 색은 이 필드 기반 under-only 강조, 분류 다색 배지는 상세/보고서 전용) / `os_eol_status`(지원 단계 — full·security_only·paid_only·ended·unknown, 카탈로그 미매칭을 "지원 중"으로 단정 안 함) / `has_operational_event`(전기간 에러 발생 유무, `get_fleet_error_hosts` 집합) |
| `ServerDetailResponse` | `to_server_detail` + `enrich_server_detail` | `os_display`(Windows 라벨 우선순위 3단 — `product_name` 연도/세대 -> `os_version` -> kernel build) / `edition`(Windows SKU pass-through, `detail.html` os_display 조합 표시) / `cpu_display` / `disk_total_gb` / `services` (ServiceItem) / `sorted_listen_ports` / `agent_id`(식별 단일 키 표시) / `cpu_arch`+`cpu_bits`(ISA·비트, pass-through) |
| `ServiceItem` | mapper | `category` (`service_classifier.classify_service`) / `matched_ports` (port 리스트) / `display_name` |
| `ListenPortItem` | mapper | `is_significant` (boolean, port < 49152 = 비동적 포트) — 매퍼가 `key_listen_ports` 를 고를 때 쓴다 |
| `MountUsageItem` | `_build_mount_item` | `mount` / `fstype` / `total_gb`·`used_gb`·`avail_gb` / `usage_pct` / `badge_class`·`bar_color` (임계값 분류) |

`ServerListItem` 의 분류 배지는 `rollup_host().host_status` 만 반영한다 — 네트워크 혼잡은 host_status 를 구동하지 않는 별개 트리거(재전송·드롭)라 분류 칼럼에 붙으면 분류의 일부로 읽히는데 목록 화면에서는 근거를 확인할 수 없다. 혼잡 확인은 서버 상세·환경 개요 도넛이다.

## 메트릭 대시보드

| ViewModel | 채우는 함수 |
|-----------|-------------|
| `MetricDashboard` | `build_dashboard`(활용률 스냅샷) + `build_saturation_signals`(자원별 포화 신호 4리스트) + `build_error_signals`(에러 fleet). 개요·자원 탭 스냅샷 카드 공용 |
| `CpuSnapshot` | jiffies delta 기반 `usage_pct`/`user_pct`/`system_pct`/`iowait_pct`. boot_time reset 시 None |
| `MemSnapshot` | 시점값 + stacked bar 누적 비율 (`cached_pct`/`buffers_pct` 100% 초과 방지 clip) — 아래 "메모리 스택바 구성 모델" |
| `DiskIoSnapshot` / `NetIoSnapshot` | rate (`d_val / dt`). reset 시 None |
| `SaturationSignal` | os-aware 포화 스냅샷 1개 — `label`/`value`/`threshold`/`unit`/`saturated`/`state`(4상태: measured·no_data·not_applicable·insufficient)/`detail`(hover). 판정은 도메인 os-aware helper 경유(임계 재계산 금지, #E3). 클라 `SignalUtils.renderSaturation` 렌더만 |
| `ErrorSignal` | 에러 축 표시자 1개(카운트형, 정상=0 발화 #E9) — `label`/`state`(4상태: clean·occurred·no_data·not_applicable — no_data 는 일시 미수집, not_applicable 은 이 OS 구조적 미지원)/`count`/`context`(종류)/`window_label`. `SignalUtils.renderErrors` 렌더 |
| `CpuCoreSnapshot` | 코어별 순간 `usage_pct` — 단일스레드 병목 실시간(Linux 전용, Windows 빈 list). CPU 상세 전용 축 |

`MetricDashboard` 추가 필드(개요·자원 탭 공용 스냅샷 카드): `disk_io`/`net_io` 는 물리 디바이스만(`device_filters` 단일 진실, LV·파티션·가상 인터페이스는 이중집계 제외) · `disk_usage_pct`(데이터 볼륨 파일시스템 used/total 집계 %, 실시간 카드 도넛) · `cpu_cores`(위 표).

메모리 스택바 구성 모델 — Used + Available = 100 인 겹치지 않는 두 축이고 Cached/Buffers 는 Available(회수 가능) 안의 세부다. 그래서 used 위 남은 공간 안에서만 cached -> buffers 를 쌓고 잔여를 free 로 채운다. used 는 `mem_used_bytes`(total-free-buff/cache)가 아니라 limit-available 로 낸다 — 전자를 쓰면 available 과 어긋나 막대 합 100 이 깨진다.

## 자원 상세 탭 '최근 N일' 평가 카드 (CPU/메모리/스토리지/네트워크, 평가 윈도우 창)

서버 세부(`/servers/{id}`) 및 4개 자원 상세 탭(`/cpu`·`/memory`·`/storage`·`/network`)이 공유하는 이용률(U)+포화(S)+에러(E) 3축 카드 — `mappers/period_assessment.build_period_assessment` 단일 산출, `services/query/server.py` 의 `get_period_assessment` 경유 각 라우터가 조회. 실시간 스냅샷 카드(순간)와 분리 — 이쪽은 `right_sizing.WINDOW_DAYS`(#F10) 통계 기준의 분류·판정 근거.

| ViewModel | 핵심 필드 |
|-----------|-----------|
| `PeriodAssessment` | `resources`([cpu, mem, disk, net] 순 `PeriodResource` 4개) / `error_rows`(전 자원 통합 `PeriodErrorRow`) / `window_days` / `classification_label`+`classification_color`(종합 배지 — `classify_host` 과 동일 단일 진실, 목록-세부 정합) |
| `PeriodResource` | `util_rows`/`sat_rows`(`PeriodSignalRow` 리스트, over 개수 = `util_over`/`sat_over`) / `has_util`(네트워크만 False — 처리량 축이라 용량% 없음) / `detail_slug` / `verdict_label`+`verdict_color`(자원별 판정, `rollup_host` 재사용) / `extra_groups`(자원별 상세 탭 전용 "신뢰도" 카드, `PeriodExtraGroup`) / `error_rows`(메모리만 채움 — `mem_` 접두 필터) / `verdict_label2`+`verdict_color2`(스토리지 전용 — 용량 축과 독립된 성능/IO 축 2번째 배지, 나머지 자원은 빈 문자열) |
| `PeriodSignalRow` | `label`/`value`/`threshold`(형식화 문자열, 템플릿 계산 0)/`over`(임계 이상)/`measured`(False면 N/A muted) |
| `PeriodExtraGroup` | `label`("부하 신호"/"통계 신뢰도" 등) + `rows`(`PeriodSignalRow`) — 성격별 그룹 |
| `PeriodErrorRow` | `key`(`mem_oom` 등 — 자원별 탭이 자기 자원 접두만 필터)/`label`/`badge_text`+`badge_class`/`note`/`sizing_signal`(OOM 발생 시 "메모리 자원 부족", 그 외 "") |

스토리지 "사용률" 행만 다른 자원(호스트 p95 집계)과 다른 산식 — worst-mount(가장 채워진 마운트 1개, `disk_worst_mount` 파라미터로 라벨에 마운트명 병기) — 실시간 카드 도넛(`disk_usage_pct`, 전체 마운트 가중평균)과 의도적으로 다른 값(#F9 명시 표기).

단일 서버 보고서(`/servers/{id}/report?view=engineer`)는 같은 카드를 `EnvironmentReportSummary.period_assessment` 에 발행 시점 스냅샷으로 얼려 CPU/메모리/스토리지/네트워크 상세 카드로 노출한다 (#C1).

## 스토리지 레이아웃 트리 / 네트워크 인터페이스 정보

| ViewModel | 채우는 mapper | 핵심 파생 |
|-----------|---------------|-----------|
| `StorageNode` | `build_storage_tree(block_devices, lvm_vgs, filesystems)` | 물리 디스크 루트 트리 — `kind`(block_device type 또는 파생 `unallocated`/`vg_free`)+`kind_label` / `meta`(계층 속성 한 줄) / 마운트 노드만 사용량 2축(`usage_pct`+`usage_label`+`usage_class`, `inode_pct`+`inode_label`+`inode_class`) / `gauge_info_width_px`(깊이 들여쓰기 상쇄, 게이지 x좌표 통일). 다중 부모(RAID span·striped VG)는 순수 트리가 안 돼(DAG) 최초 도달 디스크 그룹에만 펼치고 나머지 디스크에는 "상세는 X 아래" 참조 스텁을 남긴다 |
| `NetworkInterfaceInfo` | `build_network_interfaces` | 물리 인터페이스만(`device_filters.is_virtual_interface`) 정적 구성 — `mac`/`mtu`/`speed_mbps`/`gateway`/`dns`/`addresses`(`NetIfaceAddress` — CIDR+`is_ipv4`+`origin`). 활동(RX/TX/pps)은 `NetIoSnapshot` 별개 축(실시간 카드) |

`StorageNode` 계층별 속성 귀속 — 디스크=특성(SSD/HDD·partition_table)·배정 용량 / 파티션·LV=fstype·mount·소속 VG·segtype / fs(마운트 노드)=사용량 2축(bytes+inode) / VG=free_bytes(확장 여력) / RAID·crypt=표식. 트리 노드를 추가할 때 어느 계층에 무엇을 실을지의 기준이다.

스토리지 용량 두 축은 산식이 달라 값이 다른 것이 정상이다 — `storage_layers_gb`(배정 블록/볼륨 배정/미할당)는 lsblk 블록크기 축이고, 스토리지 탭 "파일시스템 총 용량"(`StorageDetailResponse.fs_total_gb`)은 df used+free 실용량 축이다. fs 포맷 오버헤드만큼 df 쪽이 작게 나오며 화면은 라벨로 둘을 구분한다.

`StorageDetailResponse.tree`/`NetworkDetailResponse.interfaces_info` 가 각각 소비. 둘 다 `os_family`(N/A 표시 OS 분기, #E6 `data-os-family`) 동반.

단일 서버 보고서(engineer)는 같은 트리·인터페이스를 `EnvironmentReportSummary.storage_tree`/`network_interfaces` 에 얼려 스토리지 상세 카드가 마운트별 표 대신 이 트리를 노출한다.

## 보고서·산출물

| ViewModel | 채우는 mapper |
|-----------|---------------|
| `ReportRowItem` | `to_report_row_item` — `role`(`infer_role`, listen 보강) / `recommendation`(`classify_host`) / `recommendation_label` (한국어) / `badge_class` (`rec-{enum}`) / `root_cause_label`(`rollup_host` 인과 종합) / `net_status_label`(네트워크 품질 정상·혼잡·미측정, 사이징과 별개) / `os_display` / `internal_ip[0]`. 특징 워크로드(baseline 제외): `workload_categories`(카테고리 집합) / `signature_workload_categories`(`SIGNATURE_CATEGORIES` 부분집합 — 세부 서버 목록 "구동 서비스" 열 전용, 서버 목록 뱃지와 동일 기준) / `workload_services`(카테고리별 서비스명) — 환경/N대 집계·세부 목록 뱃지 공유. 구동 서비스 차등(개별 보고서, `_build_workload_display`, baseline 포함 전부): `workload_groups`(customer 카테고리별 제품명) / `listen_ports_detail`(engineer Listen 포트 카드 — listen 소켓 원시 표). `os_eol`/`os_eol_status`(`lookup_os_eol` 판정 — 보고서 발행 기준 시각이고 live "오늘" 이 아니다) / `has_operational_event`(보고서 window 기준 `get_latest_errors` — 세부 서버 목록 전용, caller 가 `get_report(fetch_operational_events=True)` 로 명시 요청할 때만 계산, 기본 False — 환경 전체 스코프는 N+1 회피로 미계산) |
| `ReportSummary` | `QueryService.get_report` — `rows: list[ReportRowItem]`(`sort_rows_for_report` 위험 우선 정렬) + KPI 집계 (`total`/`online`/`risk_attention`(과다+유휴)/`risk_high`(부족)) + N대 선택 맥락 `os_family_summary`/`workload_summary`(`build_selection_context`) |
| `MetricSeriesItem` | `to_metric_series_item` — chart API 응답 |
| `DistributionBar` | `_to_distribution_bars` — 구성 분포 1 segment(환경 보고서 "OS 구성"). `pct`(분포 내 최대 count 대비 비율)는 어느 화면도 표시하지 않고 발행된 보고서 스냅샷의 복원 호환 때문에 채운다 — 지우면 과거 스냅샷 역직렬화가 깨진다 |

## 환경 개요 상단 요약 (overview, `/`)

환경 개요(`/`)가 노출하는 것은 `EnvironmentOverview` 하나다. `AttentionSignals`(운영신호 3 카탈로그 — 통신끊김/OS지원종료/에이전트재시작, `get_attention_signals`)는 보고서 경로에서만 소비 — 단일/선택은 `attention_for_host`/`attention_by_host`(`services/report/generator.py` 정의, `report_page.py` 소비), 환경·선택 요약은 `EnvironmentReportSummary.attention`(OS 지원종료 표). 독립 라이브 카드로는 렌더되지 않는다.

| ViewModel | 채우는 mapper | 데이터 소스 | 시간 축 | 색상 톤 |
|-----------|---------------|-------------|---------|---------|
| `EnvironmentOverview` | `build_environment_overview` — 화면 축 6: 환경 요약 KPI(대수·온라인/오프라인·자원 합계·`os_distribution`·OS 지원 4분기 `os_eol_passed`(무상 패치 종료 — paid_only·ended 합산)/`os_eol_security_only`/`os_eol_unknown`/`os_eol_supported`) / 주요 워크로드(`role_distribution` 시그니처 카테고리 인스턴스 분포 — `SIGNATURE_CATEGORIES`, 호스트 dedup 아님·0 포함 + `workload_donut`·`workload_total`·`role_unknown_count`) / 활용률(`utilization` 평균 3축 + `utilization_p95` 환경 보고서 소비, 표본 `util_sample_size`) / 포화 4도넛(`saturation_donuts` — CPU 포화·메모리 압박·디스크 I/O 포화·네트워크 혼잡) / 에러 fleet(`error_fleet` — MCE·OOM·EDAC·디스크·NIC 에러 발생 호스트 수) / 분류 도넛(`risk_donut`·`risk_donut_total`·`risk_high_count`) | `list_server_ids` + `get_servers` + `get_environment_utilization` + `get_report_aggregate` + `get_fleet_error_summary` + Redis `online:*` mget | 자원 적정성 창 (`WINDOW_DAYS`, #F10) | slate (`#f8fafc`) |
| `FleetErrorItem` | 환경 fleet 에러 표시자 1개 — `label`/`affected`(발생 호스트 수)/`total`(표본). 정상=0 발화(#E9), 카운트형이라 도넛 아닌 표시자. `_build_error_fleet` | `get_fleet_error_summary` | 전체 기간 (에러는 드문 이벤트라 창 제한 안 함) | — |
| `UtilizationBar` | `build_environment_overview` 안에서 평균 3종(CPU·메모리·디스크) + p95 2종(CPU·메모리) 생성 — `pct`/`bar_color`(단색 푸른, 값 무관)/`dash_length`(SVG dasharray, mapper 비례 산술). 디스크는 p95 를 내지 않는다 — Windows 물리 디스크 인식이 불완전해 평균만 노출 | `get_environment_utilization(WINDOW_DAYS, end)` SQL — CPU·메모리·디스크 모두 capacity-weighted (sum(used)/sum(total), 자원 총량 가중 — 서버 1대=1표 아님) | 평가 윈도우 | 테마색1 `var(--color-title)`·`None` 회(`#cbd5e1`) |
| `RiskDonutSegment` | `build_risk_donut_segments` — 5 카테고리 (under/over/idle/optimal/insufficient) `key`/`label`/`color`/`count`/`dash_length`/`dash_offset` (multi-segment 누적 음수) | `get_report_aggregate(WINDOW_DAYS)` + net baseline 주입 -> `build_resource_stats` -> `classify_host` | 평가 윈도우 USE Method | `UTIL_GAUGE_COLOR` 테마 단색 (분류는 라벨이 전달, E8) |
| `AttentionRow` (gap) | `to_gap_warning_item(raw, now)` — `badge_text`(경과 분 `{gap_min}분`) / `badge_class`(`attn-active`, 운영신호 통신끊김) | `get_metric_gap_warnings` 단일 SQL | 5min~24h 갭 | blue (`#eff6ff`) |
| `CapacityWarningItem` | `to_capacity_warning_item(raw)` — `active_causes`(발화 원인 os-neutral 라벨, `_CAUSE_LABEL_BY_TRIGGER` 파생 — 환경 요약 원인 집계 `_under_cause_summary` 단일 소스)·`recommendation_action`(자원별 독립 처방, `under_prescription` — 인과 결합이어도 관측된 under 자원 전부)·`root_cause_label`(진단 근거 전용, 처방을 거르지 않음)·`net_status_label`+`net_status_color`(네트워크 품질 전용 필드 — `action_targets_table` 전용)·`disk_io_status_label`+`disk_io_status_color`(디스크 I/O 상태, network 와 동형)·`spec_display`(정적 배정 사양 "4코어 · 8.00GB · 100GB", `host_display.spec_display_line` 단일 진실 — `ServerListItem.spec_display` 와 동일 산식, 표 호스트 옆 노출). caller가 `under_provisioned` 필터링 -> EnvironmentOverview.under_provisioned_hosts (운영신호 아님, USE Method) | `get_report_aggregate(WINDOW_DAYS)` + `build_resource_stats` -> `rollup_host`(triggers) | 평가 윈도우 USE Method | blue (`#eff6ff`) |
| `AttentionRow` (os_eol) | `to_os_eol_warning_item(raw, now)` — `resolve_os_eol`(endoflife 카탈로그) 무상 보안 패치 종료 시 반환 (운영신호) | `os_id`/`os_version`/`kernel_version` + endoflife 스냅샷 카탈로그 | endoflife.date 스냅샷 (Linux distro + Windows Server build) | blue (`#eff6ff`) |
| `AttentionRow` (agent_unstable) | `to_agent_unstable_item(public_id, hostname, restart_count)` — caller가 임계 필터링 | `get_agent_restart_counts_recent` SQL (`server_inventory_history` `agent_started_at` DISTINCT-1) | 1h fixed 윈도우 (Redis sliding 대체) | blue (`#eff6ff`) |
| `AttentionSignals` | `QueryService.get_attention_signals` 묶음 (내부 `_assemble_attention` 조립) — 운영신호 3 카탈로그(gap·os_eol·agent_unstable). `has_any` property로 빈 카드 분기 | 위 3 builder(gap/os_eol/agent_unstable) | — | blue (`#eff6ff`) |

신호 임계값 단일 정의 (mapper·service 모듈 상단):
- `_USAGE_DANGER_PCT` / `_USAGE_WARN_PCT` — 사용률 위험·주의 두 단계 (mapper). disk_warning 과 서버 상세 badge 공통
- `_UTIL_DONUT_CIRC` — `2*pi*_DONUT_RADIUS`(템플릿 SVG r 과 정합) 단일 진실 (mapper, E8)
- `_DONUT_SEGMENT_DEFS` — 자원 적정성 5 카테고리 (분류 enum·색·조치 설명). 세그먼트 키가 곧 `Recommendation` 값이라 별도 매핑 dict 를 두지 않는다. 한국어 분류명은 `right_sizing.RECOMMENDATION_LABEL_KO`, 배지 CSS 는 `mappers/constants.BADGE_CLASS` 단일 진실
- `_CAUSE_LABEL_BY_TRIGGER` — trigger key -> os-neutral 원인 라벨 (자원 부족 원인 집계 단일 진실, mapper)
- `_WORKLOAD_COLORS` — 주요 워크로드 도넛 세그먼트 색 (`SIGNATURE_CATEGORIES` 6종 hex, mapper). `app.css` 의 `.badge-cat-*` 뱃지 색과 같은 값을 보여야 한다 — SVG stroke 는 hex 를 요구해 CSS 클래스를 재사용할 수 없고 두 소스가 갈라지므로, 카테고리 색 변경 시 양쪽 동시 갱신 의무
- `agent_restart_alert_threshold` — 1h 윈도우 재시작 임계 (WebSettings, 값은 `docs/reference/contracts/env.md`)
- 디스크 용량·I/O 임계 — 본 목록 아님(운영신호가 아닌 USE Method 분류 축, `right_sizing` 모듈 단일 진실)
- `_eol_info` (mapper, os_eol) — endoflife.date 스냅샷 카탈로그(`os_eol_catalog.json`) 조회 + 경계 3개로 지원 단계 판정 단일 진실.

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

  발행된 보고서 스냅샷에는 옛 어휘(`eol`·`extended`·`supported`)를 담은 행이 남아 있어 복원 시 위 어휘로 옮긴다(`report/serializer._LEGACY_OS_EOL_STATUS`). 표시 계층은 현행 값만 분기한다 — 어휘를 바꾸면 이 매핑도 함께 늘려야 과거 이력 화면이 살아 있다.

활용률 게이지 색 카탈로그 (mapper 상수):
- `UTIL_GAUGE_COLOR = "var(--color-title)"` (constants 단일 진실, attention `_UTIL_COLOR_GAUGE` alias) — 주색(테마색1). 활용률 정도는 게이지 길이(`dash_length`)로, 색은 값 무관 단일. Right-sizing 과다프로비저닝(`_DONUT_SEGMENT_DEFS` over)·서버목록 `.rec-over_provisioned` 배지가 동일 주색 공유 (테마 통일, static-assets.md "색 테마"). under(`#ef4444`)와 대비.
- `_UTIL_COLOR_NONE = "#cbd5e1"` — 표본 부재 (회색).

## 네트워크 토폴로지 그래프 elements

`NetworkTopology.elements` 는 Cytoscape.js elements 형식(`{"data": {...}}` 리스트)이다. mapper 가 precompute 하고
템플릿은 `| tojson`, `network-topology.js` 는 레이아웃·스타일·클릭 바인딩만 한다 (P4).

`collapsed` 클래스가 붙은 요소는 초기 숨김이고 subnet 노드 클릭이 펼친다 — 화면 의도는
`docs/explanation/products/dashboard.md`.

| 종류 | id / source-target | data | class |
|------|--------------------|------|-------|
| gateway 노드 | `gw:<gw>` | `label`(gw) · `kind` "gateway" · `subnetCount` | — |
| subnet 노드 | `subnet:<net>` | `label`(net) · `kind` "subnet" · `hostCount` · `gateway` | — |
| host 노드 | `host:<public_id>` | `label`(hostname) · `kind` "host" · `publicId` · `osFamily` · `roles` · `multiHomed`(2+ 서브넷) · `ifaces[{name,mac,mtu,gateway}]`(노드 툴팁) | `collapsed` |
| route 엣지 | `gw:<gw>` -> `subnet:<net>` | `kind` "route" | — |
| member 엣지 | `host:<public_id>` -> `subnet:<net>` | `kind` "member" | `collapsed` |

그래프 노드·엣지 조립이 mapper 소관인 이유는 결정론적 표현 변환이기 때문이다 — 같은 인벤토리에서 같은 그래프가
나오므로 클라가 매번 다시 만들 이유가 없다 (P2).
