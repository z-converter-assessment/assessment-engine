# ViewModel 카탈로그

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

`InventoryExportEntry`는 `db/repositories/outbound.py` (vendor 중립 vendor JSON 응답 — ViewModel 아님). `to_inventory_export_entry`가 변환.

## 목록 화면 상단 요약 (list.html)

대시보드 첫 페이지 + 검색·필터 미사용일 때만 두 섹션 노출 — `EnvironmentOverview`(환경 요약 KPI + 활용률 도넛 + 프로비저닝 분포 도넛) + `AttentionSignals`(통합 신호 카드 6종). 검색 결과·페이지네이션 화면에선 자동 격리(라우터 분기).

시간 축은 F11 단일 윈도우 — `recommendation.WINDOW_DAYS=14`.

| ViewModel | 채우는 mapper | 데이터 소스 | 시간 축 | 색상 톤 |
|-----------|---------------|-------------|---------|---------|
| `EnvironmentOverview` | `build_environment_overview(details, online_count, utilization, risk_counts)` — `total`/`online`/`offline`/`total_vcpus`/`total_memory_gb`(float)/`total_disk_gb`/`role_distribution`/`utilization`/`util_sample_size`/`risk_donut`/`risk_donut_total`/`risk_high_count` | `list_server_ids` + `get_servers` + `environment_utilization(WINDOW_DAYS)` + `report_aggregate(WINDOW_DAYS)` + Redis `online:*` mget | 14일 평균 + 분류 | slate (`#f8fafc`) |
| `UtilizationBar` | `build_environment_overview` 안에서 3종 (CPU·메모리·디스크) 생성 — `pct`/`bar_color`(임계 분기)/`dash_length`(SVG dasharray, mapper 비례 산술) | `environment_utilization(WINDOW_DAYS)` SQL | 14일 평균 | 임계별 (`<60` 녹·`60~80` 노·`>=80` 빨·`None` 회) |
| `RiskDonutSegment` | `build_risk_donut_segments` — 3 카테고리 (under/over/normal) `key`/`label`/`color`/`count`/`dash_length`/`dash_offset` (multi-segment 누적 음수) | `report_aggregate(WINDOW_DAYS)` -> `recommendation.classify` -> `_DONUT_SEGMENT_FROM_REC` | 14일 USE Method | 빨/노/녹 |
| `DiskWarningItem` | `to_disk_warning_item` — `used_pct` / `free_gb` / `total_gb` / `last_metric_at` / `badge_class` | `disk_usage_warnings(threshold_pct, limit)` 단일 SQL | 7d 윈도우 mount당 latest | blue (`#eff6ff`) |
| `GapWarningItem` | `to_gap_warning_item(raw, now)` — `gap_minutes` / `badge_class` | `metric_gap_warnings(gap_min, recent_h, limit)` 단일 SQL | 5min~24h 갭 | blue (`#eff6ff`) |
| `CapacityTriggerBadge` | `to_capacity_warning_item` 안에서 3종 (스왑·CPU·메모리) 생성 — `label`/`color`(hue 분리)/`active`(임계 초과 여부) | `_CAPACITY_TRIGGER_COLORS` 단일 색 진실 | — | 빨강(`#dc2626`)/파랑(`#2563eb`)/보라(`#8b5cf6`) |
| `CapacityWarningItem` | `to_capacity_warning_item(raw)` — `cpu_p95_pct`/`mem_p95_pct`/`swap_used`/`triggers`. caller가 `under_provisioned` 필터링 | `report_aggregate(WINDOW_DAYS)` + `recommendation.classify` | 14일 USE Method | blue (`#eff6ff`) |
| `DiskDaysWarningItem` | `to_disk_days_warning_item(public_id, hostname, mount, days, used_pct)` | `report_mount_worst(WINDOW_DAYS)` fill_rate 추정 | 14일 fill_rate -> N일 후 full | blue (`#eff6ff`) |
| `OSEolWarningItem` | `to_os_eol_warning_item(raw)` — `_OS_EOL` 정적 매핑 매칭 시 반환 | inventory `os_id`/`os_version` + 정적 dict | 정적 (CentOS 7·RHEL 7·Ubuntu 18.04·Debian 10 등) | blue (`#eff6ff`) |
| `AgentUnstableItem` | `to_agent_unstable_item(public_id, hostname, restart_count)` — caller가 임계 필터링 | Redis `agent_restarts:{sid}` mget | 1h 슬라이딩 윈도우 | blue (`#eff6ff`) |
| `AttentionSignals` | `query_service.get_attention_signals` 묶음 (6 카탈로그). `has_any` property로 빈 카드 분기 | 위 6 메서드 | — | blue (`#eff6ff`) |

신호 임계값 단일 정의 (mapper·service 모듈 상단):
- `_USAGE_DANGER_PCT = 90` — disk_warning 공통 (mapper)
- `_USAGE_WARN_PCT   = 75` — 위험도 분류 보조
- `_GAP_DANGER_MINUTES = 30` — gap_warning 위험 색 (mapper)
- `_UTIL_LOW_PCT = 60` / `_UTIL_HIGH_PCT = 80` — 활용률 도넛 색 임계 (mapper, E10)
- `_UTIL_DONUT_CIRC = 263.89` — SVG 원주 r=42 단일 진실 (mapper, E10)
- `_DONUT_SEGMENT_FROM_REC` / `_DONUT_SEGMENT_DEFS` — 프로비저닝 도넛 3 카테고리 단일 매핑
- `_CAPACITY_TRIGGER_COLORS` — capacity trigger 3종 hue 분리 단일 색 (mapper)
- `disk_threshold_pct = 85` — disk_warnings 진입 임계 (service 기본값)
- `days_until_full_threshold = 30` — 디스크 잔여 신호 진입 임계 (service 기본값)
- `agent_restart_alert_threshold = 3` — 1h 윈도우 재시작 임계 (web_settings)
- `_OS_EOL` — `(os_id, os_version_prefix) -> EOL 날짜 string` 정적 dict (mapper)

## dataclass 필드 순서 주의 (F1)

default 있는 필드는 default 없는 필드 뒤에. 안 그러면 `non-default argument follows default` `TypeError` 즉시 발생. 새 ViewModel 추가 시 항상 점검.
