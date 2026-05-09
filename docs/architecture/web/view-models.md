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

## dataclass 필드 순서 주의 (F1)

default 있는 필드는 default 없는 필드 뒤에. 안 그러면 `non-default argument follows default` `TypeError` 즉시 발생. 새 ViewModel 추가 시 항상 점검.
