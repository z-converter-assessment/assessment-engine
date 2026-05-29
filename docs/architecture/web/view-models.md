# Web ViewModel 카탈로그

정책: CLAUDE.md #E3 (mapper 단일 변환) · #E8 (차트·도넛 UI). 본 문서는 ViewModel 카탈로그·신호 임계값 단일 정의. 신규 파생 필드 추가 시 `cache_serializer._DETAIL_DISPLAY_FIELDS` 동기화 필수.

## 서버 표시

| ViewModel | 채우는 mapper | 핵심 파생 |
|-----------|---------------|-----------|
| `ServerListItem` | `to_server_list_item` | `os_display` / `mem_total_gb` / `storage_total_gb` / `is_online` / `known_services` (카테고리 dedup) / `show_unknown_badge` |
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
| `ReportRowItem` | `to_report_row_item(raw, online)` — `role`(`infer_role`) / `recommendation`(`recommendation.classify`) / `recommendation_label` (한국어) / `badge_class` (`rec-{enum}`) / `os_display` / `internal_ip[0]` |
| `ReportSummary` | `query_service.get_report` — `rows: list[ReportRowItem]` + KPI 집계 (`total`/`online`/`over`/`under`) |
| `MetricSeriesItem` | `to_metric_series_item` — chart API 응답 |

`InventoryExportEntry`는 `db/dtos/outbound.py` (vendor 중립 vendor JSON 응답 — ViewModel 아님). `to_inventory_export_entry`가 변환.

## 목록 화면 상단 요약 (list.html)

대시보드 첫 페이지 + 검색·필터 미사용일 때만 두 섹션 노출 — `EnvironmentOverview`(환경 요약 KPI + 활용률 도넛 + 프로비저닝 분포 도넛) + `AttentionSignals`(통합 신호 카드 6종). 검색 결과·페이지네이션 화면에선 자동 격리(라우터 분기).

시간 축은 F11 단일 윈도우 — `recommendation.WINDOW_DAYS=14`.

| ViewModel | 채우는 mapper | 데이터 소스 | 시간 축 | 색상 톤 |
|-----------|---------------|-------------|---------|---------|
| `EnvironmentOverview` | `build_environment_overview(details, online_count, utilization, risk_counts)` — `total`/`online`/`offline`/`total_vcpus`/`total_memory_gb`(float)/`total_disk_gb`/`os_distribution`(os_family별 수)/`role_distribution`(전체 서비스 카테고리 카운트, 대표 1개 아님)/`utilization`/`util_sample_size`/`risk_donut`/`risk_donut_total`/`risk_high_count` | `list_server_ids` + `get_servers` + `environment_utilization(WINDOW_DAYS)` + `report_aggregate(WINDOW_DAYS)` + Redis `online:*` mget | 14일 평균 + 분류 | slate (`#f8fafc`) |
| `UtilizationBar` | `build_environment_overview` 안에서 3종 (CPU·메모리·디스크) 생성 — `pct`/`bar_color`(단색 푸른, 값 무관)/`dash_length`(SVG dasharray, mapper 비례 산술) | `environment_utilization(WINDOW_DAYS)` SQL — CPU·메모리·디스크 모두 서버별 평균 후 서버간 평균 (서버 1대=1표) | 14일 평균 | 단색 푸른(`#3b82f6`)·`None` 회(`#cbd5e1`) |
| `RiskDonutSegment` | `build_risk_donut_segments` — 6 카테고리 (under/over/idle/shutdown/optimal/insufficient) `key`/`label`/`color`/`count`/`dash_length`/`dash_offset` (multi-segment 누적 음수) | `report_aggregate(WINDOW_DAYS)` -> `recommendation.classify` -> `_DONUT_SEGMENT_FROM_REC` | 14일 USE Method | `_DONUT_SEGMENT_DEFS` 색 (E8) |
| `DiskWarningItem` | `to_disk_warning_item` — `used_pct` / `free_gb` / `total_gb` / `last_metric_at` / `badge_class` | `disk_usage_warnings(threshold_pct, limit)` 단일 SQL | 7d 윈도우 mount당 latest | blue (`#eff6ff`) |
| `GapWarningItem` | `to_gap_warning_item(raw, now)` — `gap_minutes` / `badge_class` | `metric_gap_warnings(gap_min, recent_h, limit)` 단일 SQL | 5min~24h 갭 | blue (`#eff6ff`) |
| `CapacityTriggerBadge` | `to_capacity_warning_item` 안에서 3종 (스왑·CPU·메모리) 생성 — `label`/`color`(hue 분리)/`active`(임계 초과 여부) | `_CAPACITY_TRIGGER_COLORS` 단일 색 진실 | — | 빨강(`#dc2626`)/파랑(`#2563eb`)/보라(`#8b5cf6`) |
| `CapacityWarningItem` | `to_capacity_warning_item(raw)` — `cpu_p95_pct`/`mem_p95_pct`/`swap_used`/`triggers`. caller가 `under_provisioned` 필터링 | `report_aggregate(WINDOW_DAYS)` + `recommendation.classify` | 14일 USE Method | blue (`#eff6ff`) |
| `DiskDaysWarningItem` | `to_disk_days_warning_item(public_id, hostname, mount, days, used_pct)` | `report_mount_worst(WINDOW_DAYS)` fill_rate 추정 | 14일 fill_rate -> N일 후 full | blue (`#eff6ff`) |
| `OSEolWarningItem` | `to_os_eol_warning_item(raw, now)` — `resolve_os_eol`(endoflife 카탈로그) EOL 경과 시 반환 | `os_id`/`os_version`/`kernel_version` + endoflife 스냅샷 카탈로그 | endoflife.date 스냅샷 (Linux 11 distro + Windows Server build, ADR 0031) | blue (`#eff6ff`) |
| `AgentUnstableItem` | `to_agent_unstable_item(public_id, hostname, restart_count)` — caller가 임계 필터링 | Redis `agent_restarts:{sid}` mget | 1h 슬라이딩 윈도우 | blue (`#eff6ff`) |
| `AttentionSignals` | `query_service.get_attention_signals` 묶음 (6 카탈로그). `has_any` property로 빈 카드 분기 | 위 6 메서드 | — | blue (`#eff6ff`) |

신호 임계값 단일 정의 (mapper·service 모듈 상단):
- `_USAGE_DANGER_PCT = 90` — disk_warning 공통 (mapper)
- `_USAGE_WARN_PCT   = 75` — 위험도 분류 보조
- `_GAP_DANGER_MINUTES = 30` — gap_warning 위험 색 (mapper)
- `_UTIL_DONUT_CIRC = 263.89` — SVG 원주 r=42 단일 진실 (mapper, E8)
- `_DONUT_SEGMENT_FROM_REC` / `_DONUT_SEGMENT_DEFS` — 프로비저닝 도넛 6 카테고리 단일 매핑
- `_CAPACITY_TRIGGER_COLORS` — capacity trigger 3종 hue 분리 단일 색 (mapper)
- `disk_threshold_pct = 85` — disk_warnings 진입 임계 (service 기본값)
- `days_until_full_threshold = 30` — 디스크 잔여 신호 진입 임계 (service 기본값)
- `agent_restart_alert_threshold = 3` — 1h 윈도우 재시작 임계 (web_settings)
- `resolve_os_eol` (mapper, shared) — endoflife.date 스냅샷 카탈로그(`os_eol_catalog.json`) 조회 + EOL 경과 판정 단일 진실 (ADR 0031). Linux: `os_id`->endoflife product slug(`_OS_ID_TO_EOL_PRODUCT`), `os_version`->cycle. Windows: `kernel build`->windows-server latest build (운영=Server 가정). attention 카드 + 보고서 정성 요약 공용.

활용률 게이지 색 카탈로그 (mapper 상수):
- `_UTIL_COLOR_GAUGE = "#3b82f6"` — 단색 푸른. 활용률 정도는 게이지 길이(`dash_length`)로, 색은 값 무관 단일 (그라데이션·임계 분기 제거). 위험도 색은 Right-sizing 도넛(`_DONUT_SEGMENT_DEFS`)이 별도 담당.
- `_UTIL_COLOR_NONE = "#cbd5e1"` — 표본 부재 (회색).

## dataclass 필드 순서 주의 (F1)

default 있는 필드는 default 없는 필드 뒤에. 안 그러면 `non-default argument follows default` `TypeError` 즉시 발생. 새 ViewModel 추가 시 항상 점검.
