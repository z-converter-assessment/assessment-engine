# Web ViewModel 카탈로그

정책: CLAUDE.md #E3 (mapper 단일 변환) · #E8 (차트·도넛 UI). 본 문서는 ViewModel 카탈로그·신호 임계값 단일 정의. 신규 파생 필드 추가 시 `cache_serializer._DETAIL_DISPLAY_FIELDS` 동기화 필수.

## 서버 표시

| ViewModel | 채우는 mapper | 핵심 파생 |
|-----------|---------------|-----------|
| `ServerListItem` | `to_server_list_item` | `os_display` / `mem_total_gb` / `storage_total_gb` / `is_online` / `known_services` (카테고리 dedup) / `show_unknown_badge` / `recommendation_label`(영어 enum 풀네임 — 도넛 범례와 동일, `_DONUT_SEGMENT_DEFS` label) / `recommendation_color` / `provisioning_class` |
| `ServerDetailResponse` | `to_server_detail` + `enrich_server_detail` | `os_display` / `cpu_display` / `disk_total_gb` / `services` (ServiceItem) / `sorted_listen_ports` |
| `ServiceItem` | mapper | `category` (`service_classifier.classify`) / `matched_ports` (port 리스트) / `display_name` |
| `ListenPortItem` | mapper | `is_well_known` (boolean) — 템플릿 분기는 이걸로 |
| `MountUsageItem` | `_build_mount_item` | `device_name` (`find_parent_disk`) / `usage_pct` / `bar_color` (임계값 분류) |

## 메트릭 대시보드

| ViewModel | 채우는 함수 |
|-----------|-------------|
| `MetricDashboard` | `metrics_calculator.build_dashboard` (DashboardRaw → CpuSnapshot/MemSnapshot/SwapSnapshot/DiskIoSnapshot/NetIoSnapshot/MountDashSnapshot) |
| `CpuSnapshot` | jiffies delta 기반 `usage_pct`/`user_pct`/`system_pct`/`iowait_pct`. boot_time reset 시 None |
| `MemSnapshot` | 시점값 + stacked bar 누적 비율 (`cached_pct`/`buffers_pct` 100% 초과 방지 clip) |
| `DiskIoSnapshot` / `NetIoSnapshot` | rate (`d_val / dt`). reset 시 None |

## 보고서·산출물

| ViewModel | 채우는 mapper |
|-----------|---------------|
| `ReportRowItem` | `to_report_row_item(raw, online)` — `role`(`infer_role`, listen 보강) / `recommendation`(`classify_host`) / `recommendation_label` (한국어) / `badge_class` (`rec-{enum}`) / `os_display` / `internal_ip[0]`. 구동 서비스 차등(개별 보고서, `_build_workload_display`): `workload_groups`(customer 카테고리별 제품명) / `service_units`(engineer unit·카테고리·귀속 포트) / `listen_ports_detail`(engineer listen 소켓) |
| `ReportSummary` | `query_service.get_report` — `rows: list[ReportRowItem]`(`sort_rows_for_report` 위험 우선 정렬) + KPI 집계 (`total`/`online`/`over`/`under`) + N대 선택 맥락 `os_family_summary`/`workload_summary`(`build_selection_context`) |
| `MetricSeriesItem` | `to_metric_series_item` — chart API 응답 |

`InventoryExportEntry`는 `db/dtos/outbound.py` (vendor 중립 vendor JSON 응답 — ViewModel 아님). `to_inventory_export_entry`가 변환.

## 환경 개요 상단 요약 (overview, `/`)

환경 개요(`/`)에서 두 영역 노출 — `EnvironmentOverview`(환경 요약 KPI + 활용률 도넛 + 프로비저닝 분포 도넛) + `AttentionSignals`(운영신호 카드 3 카탈로그 — 통신끊김/OS지원종료/에이전트재시작). 서버 목록(`/servers`)은 행만 — 화면 분리 자체가 컨텍스트 가드(#E9).

요약 위젯 윈도우는 `DASHBOARD_TIME_RANGE`(24h, #F10), right-sizing 표준 평가 윈도우는 `recommendation.WINDOW_DAYS=14` (#F10) — 의도 분리.

| ViewModel | 채우는 mapper | 데이터 소스 | 시간 축 | 색상 톤 |
|-----------|---------------|-------------|---------|---------|
| `EnvironmentOverview` | `build_environment_overview(details, online_count, utilization, risk_counts)` — `total`/`online`/`offline`/`total_vcpus`/`total_memory_gb`(float)/`total_disk_gb`/`os_distribution`(os_family별 수)/`role_distribution`(전체 서비스 카테고리 카운트, 대표 1개 아님)/`role_unknown_count`(known 역할 0인 호스트 수 — 서비스 없음·전부 unknown, 호스트 단위)/`utilization`/`util_sample_size`/`risk_donut`/`risk_donut_total`/`risk_high_count` | `list_server_ids` + `get_servers` + `environment_utilization(DASHBOARD_WINDOW_DAYS, end)` + `report_aggregate(DASHBOARD_WINDOW_DAYS)` + Redis `online:*` mget | 최근 24시간 (`DASHBOARD_TIME_RANGE`, #F10) | slate (`#f8fafc`) |
| `UtilizationBar` | `build_environment_overview` 안에서 3종 (CPU·메모리·디스크) 생성 — `pct`/`bar_color`(단색 푸른, 값 무관)/`dash_length`(SVG dasharray, mapper 비례 산술) | `environment_utilization(DASHBOARD_WINDOW_DAYS, end)` SQL — CPU·메모리·디스크 모두 capacity-weighted (Σused/Σtotal, 자원 총량 가중 — 서버 1대=1표 아님) | 최근 24시간 | 테마색1 `var(--color-title)`·`None` 회(`#cbd5e1`) |
| `RiskDonutSegment` | `build_risk_donut_segments` — 5 카테고리 (under/over/idle/optimal/insufficient) `key`/`label`/`color`/`count`/`dash_length`/`dash_offset` (multi-segment 누적 음수) | `report_aggregate(DASHBOARD_WINDOW_DAYS)` + net baseline 주입 -> `build_resource_stats` -> `classify_host` -> `_DONUT_SEGMENT_FROM_REC` | 최근 24시간 USE Method | `_DONUT_SEGMENT_DEFS` 색 (E8) |
| `AttentionRow` (gap) | `to_gap_warning_item(raw, now)` — `gap_minutes` / `badge_class` (운영신호 통신끊김) | `metric_gap_warnings(gap_min, recent_h, limit)` 단일 SQL | 5min~24h 갭 | blue (`#eff6ff`) |
| `CapacityWarningItem` | `to_capacity_warning_item(raw)` — `active_causes`(발화 원인 os-neutral 라벨, `_CAUSE_LABEL_BY_TRIGGER` 파생 — 환경 요약 원인 집계 `_under_cause_summary` 단일 소스)·`metrics`(6축 os-aware). caller가 `under_provisioned` 필터링 -> EnvironmentOverview.under_provisioned_hosts (운영신호 아님, USE Method) | `report_aggregate(DASHBOARD_WINDOW_DAYS)` + `build_resource_stats` -> `rollup_host`(triggers) | 최근 24시간 USE Method | blue (`#eff6ff`) |
| `SaturationAxis` | `_build_saturation_axes(raw, stats)` — single_report '포화 축 평가' 카드 3행(CPU/메모리/디스크 I/O). `axis`(os-neutral)/`signal`(OS별 측정 신호)/`value`/`threshold`/`status`(포화·정상·미관측)/`status_class`. os-aware helper 판정 재사용 | `report_aggregate` + `build_resource_stats` -> os-aware helper | 발행 윈도우 | — |
| `AttentionRow` (os_eol) | `to_os_eol_warning_item(raw, now)` — `resolve_os_eol`(endoflife 카탈로그) EOL 경과 시 반환 (운영신호) | `os_id`/`os_version`/`kernel_version` + endoflife 스냅샷 카탈로그 | endoflife.date 스냅샷 (Linux distro + Windows Server build) | blue (`#eff6ff`) |
| `AttentionRow` (agent_unstable) | `to_agent_unstable_item(public_id, hostname, restart_count)` — caller가 임계 필터링 | `agent_restart_counts_recent(since=now-1h)` SQL (`server_inventory_history` `agent_started_at` DISTINCT-1) | 1h fixed 윈도우 (Redis sliding 대체) | blue (`#eff6ff`) |
| `AttentionSignals` | `query_service.get_attention_signals` 묶음 (내부 `_assemble_attention` 조립) — 운영신호 3 카탈로그(gap·os_eol·agent_unstable). `has_any` property로 빈 카드 분기 | 위 3 builder(gap/os_eol/agent_unstable) | — | blue (`#eff6ff`) |

신호 임계값 단일 정의 (mapper·service 모듈 상단):
- `_USAGE_DANGER_PCT = 90` — disk_warning 공통 (mapper)
- `_USAGE_WARN_PCT   = 75` — 위험도 분류 보조
- `_GAP_DANGER_MINUTES = 30` — gap_warning 위험 색 (mapper)
- `_UTIL_DONUT_CIRC = 263.89` — SVG 원주 r=42 단일 진실 (mapper, E8)
- `_DONUT_SEGMENT_FROM_REC` / `_DONUT_SEGMENT_DEFS` — 프로비저닝 도넛 6 카테고리 단일 매핑
- `_CAUSE_LABEL_BY_TRIGGER` — trigger key -> os-neutral 원인 라벨 (자원 부족 원인 집계 단일 진실, mapper)
- `disk_threshold_pct = 85` — disk_warnings 진입 임계 (service 기본값)
- `days_until_full_threshold = 30` — 디스크 잔여 신호 진입 임계 (service 기본값)
- `agent_restart_alert_threshold = 3` — 1h 윈도우 재시작 임계 (web_settings)
- `resolve_os_eol` (mapper, shared) — endoflife.date 스냅샷 카탈로그(`os_eol_catalog.json`) 조회 + EOL 경과 판정 단일 진실. Linux: `os_id`->endoflife product slug(`_OS_ID_TO_EOL_PRODUCT`), `os_version`->cycle. Windows: `kernel build`->windows-server latest build (운영=Server 가정). attention 카드 + 보고서 정성 요약 공용.

활용률 게이지 색 카탈로그 (mapper 상수):
- `UTIL_GAUGE_COLOR = "var(--color-title)"` (shared 단일 진실, attention `_UTIL_COLOR_GAUGE` alias) — 주색(테마색1). 활용률 정도는 게이지 길이(`dash_length`)로, 색은 값 무관 단일. Right-sizing 과다프로비저닝(`_DONUT_SEGMENT_DEFS` over)·서버목록 `.rec-over_provisioned` 배지가 동일 주색 공유 (테마 통일, static-assets.md "색 테마"). under(`#ef4444`)와 대비.
- `_UTIL_COLOR_NONE = "#cbd5e1"` — 표본 부재 (회색).

## dataclass 필드 순서 주의 (F1)

default 있는 필드는 default 없는 필드 뒤에. 안 그러면 `non-default argument follows default` `TypeError` 즉시 발생. 새 ViewModel 추가 시 항상 점검.
