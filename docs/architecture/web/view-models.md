# ViewModel 카탈로그

`view_models.py` — Service → Router 표시 계층. 모든 파생 필드는 mapper에서 채움 (P2). 새 파생 필드 추가 시 `cache_serializer._DETAIL_DISPLAY_FIELDS` 동기화 필수.

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

서버 목록 첫 페이지 + 검색·필터 미사용일 때만 두 섹션 노출. 검색 결과·페이지네이션 화면에선 자동 격리(라우터 분기).

| ViewModel | 채우는 mapper | 데이터 소스 | 시간 축 | 색상 톤 |
|-----------|---------------|-------------|---------|---------|
| `RiskServerItem` | `to_risk_server_item(raw, online, disk_max_pct)` — `risk_score`(정렬 + 게이지) / `risk_score_color`(빨/노/초 임계 — mapper 단일 결정) / `primary_concern`(한국어 라벨) / `badge_class`(`rec-{enum}`) / `cpu/mem/disk_max_pct`(도넛 3개 채움) | `report_aggregate(period_days=1)` + `latest_disk_max_pct` | 24h USE 통계 (위험 무관 정렬 상위 3대) | orange (`#fff7ed`) |
| `DiskWarningItem` | `to_disk_warning_item` — `used_pct` / `free_gb` / `total_gb` / `last_metric_at` (운영자 stale 판단) / `badge_class` | `disk_usage_warnings(threshold_pct, limit)` 단일 SQL | 7d 윈도우 mount당 latest (현재 시점) | blue (`#eff6ff`) |
| `GapWarningItem` | `to_gap_warning_item(raw, now)` — `gap_minutes` / `badge_class` | `metric_gap_warnings(gap_min, recent_h, limit)` 단일 SQL | 5min~24h 갭 (단기 끊김) | blue (`#eff6ff`) |
| `AttentionSignals` | `query_service.get_attention_signals` 묶음 | 위 두 메서드 | — | — |

`RiskServerItem` 우선순위 분기 (mapper에서 결정 — P2):
1. 오프라인 → score 100, "오프라인"
2. 스왑 활성 → score 95, "스왑 활성"
3. MEM p95 ≥ 90 → score 90 (MEM이 CPU보다 우선 — OOM 위험 큼)
4. CPU p95 ≥ 90 → score 85
5. MEM p95 ≥ 75 → score 60
6. CPU p95 ≥ 75 → score 55
7. 그 외 → `None` 반환 (목록 미노출)

임계값 단일 정의 (mapper 모듈 상단):
- `_USAGE_DANGER_PCT = 90` — risk_top·disk_warning 공통
- `_USAGE_WARN_PCT   = 75` — risk_top
- `_GAP_DANGER_MINUTES = 30` — gap_warning

## dataclass 필드 순서 주의 (F1)

default 있는 필드는 default 없는 필드 뒤에. 안 그러면 `non-default argument follows default` `TypeError` 즉시 발생. 새 ViewModel 추가 시 항상 점검.
