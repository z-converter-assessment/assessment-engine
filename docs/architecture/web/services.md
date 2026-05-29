# Web 서비스 계층 모듈

정책: CLAUDE.md #E3 · #E7 (도메인 분류) · #F10 (평가 윈도우). 본 문서는 service 모듈 카탈로그·서비스 분류·Recommendation·대시보드 상단 요약 단일 진실.

| 모듈 | 책임 |
|------|------|
| `query_service.py` | Redis 캐시 + repository 오케스트레이션. SSR/JSON 양 경로에 일관된 ViewModel·Summary 반환 |
| `task_service.py` | Task 발행 (DB INSERT + Redis SET). 트랜잭션 경계 + `IntegrityError` -> `TaskDuplicatePending` 변환. 본 모듈 상단 `HttpZdmPackageResolver` (ZDM 패키지 sha256·size 동적 조회 — install 발행 의존성) |
| `diagnostic_service.py` | 진단 job 발행 (input_hash 계산·INSERT·RabbitMQ publish) + polling 조회 + SSR latest fetch. 추상 `BaseDiagnosticRepository` + `BaseQueryRepository`만 의존 (F4). 트랜잭션 경계 자체 관리 — router는 service만 호출. ADR 0004 + 0010 |
| `mappers/` (sub-package) | Outbound DTO + Detail -> ViewModel 변환 단일 진실 (P2). 11 sub-module — `server.py` / `metric.py` / `attention.py` / `report.py` / `export.py` / `task.py` / `shared.py` (공용 임계 상수 + ReportView Literal + `_DONUT_SEGMENT_DEFS` + `resolve_os_eol`/endoflife 카탈로그 ADR 0031) / `diagnostic.py` (진단 결과 표시 파생 — `to_view` / `to_panel_payload` / `to_history_item`) / `environment_report.py` (환경 보고서 합성) / `report_history.py` (보고서 이력 row) |
| `metrics_calculator.py` | CPU/Disk/Net delta + Mem/Swap 시점값 -> Snapshot. `_is_counter_reset` (boot_time 비교) |
| `cache_serializer.py` | Redis serde — `ServerDetailResponse` / `MetricDashboard`. 역직렬화 후 `enrich_*` 재호출 (idempotent) |
| `unit_converter.py` | KB->GB / sectors->KB/s / usage_pct 단위 변환 |
| `device_filters.py` | 물리 디스크·LVM·partition 분류 + 가상 마운트 필터 + `find_parent_disk` (mount-disk 조인) |
| `service_classifier.py` | systemd unit -> 서비스 카테고리 (`web`/`db`/`cache`/`mq`/`monitor` 등) + 포트 매핑 |

진단 deep dive(워커·스케줄러·LLM 토글·diagnostic_jobs): `docs/architecture/diagnostic.md`.

## 서비스 분류 — 3단계 표시 계층

| 단계 | 페이지 | 표시 |
|------|--------|------|
| 목록 | `/servers/` | 카테고리 badge만 (`web`/`db`/...) — 원본 unit 노출 안 함 |
| 상세 | `/servers/{id}` | unit 이름 + matched_ports + 카테고리 badge |
| services 탭 | `/servers/{id}/services` | unit 전체 + sub state + 포트 + 카테고리 |

`service_classifier.classify(unit)` — keyword substring 매칭. 분류 실패 시 `"unknown"`. role 추론(`infer_role`)도 같은 함수 활용 — 가장 빈도 높은 카테고리.

## mount - disk 매핑 (Linux 디바이스 식별 표준)

`device_filters.find_parent_disk(mount_major, mount_minor, disks)`:
- `mount.major == disk.major AND mount.minor == disk.minor` → 디스크 자체 마운트
- `disk.minor + 1..15` → 그 디스크의 partition (SCSI/virtio 관례)
- 가상 (major=0, tmpfs) → None

storage 페이지 mount → disk 매칭 + `_split_disks` (Inventory JSON Export의 `additional_disks.mount_hint`)에 활용.

## Recommendation 분류 — USE Method 출처

도메인 모듈: `assessment_engine/recommendation.py` (web·diagnostic 양쪽 import). `WINDOW_DAYS=14` 평가 윈도우(#F10)·USE Method 임계값 모두 본 모듈 코드 단일 진실(모듈 상단 명명 상수).

임계값 출처 주석 명시:
- AWS Compute Optimizer: idle (CPU peak <=1%) / over (CPU p95 <=30%, MEM p95 <=50%)
- Azure Advisor: shutdown (CPU p95 <=3%, NET <=2Mbps)
- GCP Recommender: headroom 30%
- Kleinrock 큐잉 이론(1975): under (CPU p95 >=70%)
- Linux page cache: under (MEM p95 >=80%)

UI badge 임계값(`mappers/shared.py` `_USAGE_DANGER_PCT`/`_USAGE_WARN_PCT`)과는 별 도메인 — 시점 사용량 시각 신호 vs 통계 right-sizing 결정.

OS 분기 (원칙 P2/P4): `classify`는 `ResourceStats.os_family`로 OS별 신호 의미를 분기한다. swap은 Linux page-out(메모리 압박) 신호이나 Windows pagefile은 여유 RAM에도 상시 사용되는 baseline이라 saturation이 아니므로, `recommendation.swap_saturation(os_family, swap_used)` 단일 helper가 Windows에서 swap 축을 제외한다 (classify·report mapper·attention 배지·환경 swap_pressure 카운트 모두 본 helper 경유). saturation 축(load/iowait)도 Windows는 OS 부재라 utilization 축만으로 분류 — `is_partial_evaluation`이 True를 반환해 보고서가 "부분 평가" 마커 표시(`ReportRowItem.is_partial` precompute, 템플릿은 bool만 분기). os_family None(unknown)은 Linux로 취급해 기존 동작 보존. OS 분기·판정 순서 상세는 `right_sizing_thresholds.html` 참고자료 단일 진실.

## 대시보드 상단 요약 — environment_overview + attention

`/servers/` 첫 페이지에서 두 영역으로 표시. environment_overview는 환경 현황·평균·분포(도넛), attention은 즉시 조치 신호 카드. 시간 축은 F11 단일 윈도우(`recommendation.WINDOW_DAYS=14`).

| 시선 | service 메서드 | repo SQL | 시간 축 | 분류 |
|------|----------------|----------|---------|------|
| environment_overview | `get_environment_overview()` | `list_server_ids` + `get_servers` + `environment_utilization(WINDOW_DAYS)` + `report_aggregate(WINDOW_DAYS)` + Redis online mget | 14일 USE Method + 14일 평균 활용률 | 자원 합계·역할 분포·활용률 도넛·프로비저닝 분포 도넛 |
| attention.capacity_warnings | `get_attention_signals` | `report_aggregate(WINDOW_DAYS)` + `recommendation.classify` | 14일 USE Method | `under_provisioned` 서버 — trigger 3종 (스왑·CPU·메모리) 활성/비활성 |
| attention.disk_warnings | `get_attention_signals` | `disk_usage_warnings(threshold_pct=85)` 단일 SQL | 7d 안 mount latest (현재) | 사용률 >=85% |
| attention.gap_warnings | `get_attention_signals` | `metric_gap_warnings(gap_min=5, recent_h=24)` 단일 SQL | 5min~24h 갭 (단기) | "한때 살아있다 끊김" |
| attention.days_until_full_warnings | `get_attention_signals` | `report_mount_worst(WINDOW_DAYS)` fill_rate 추정 | 14일 fill_rate | days_until_full <= 30일 |
| attention.os_eol_warnings | `get_attention_signals` | inventory + `resolve_os_eol`(endoflife.date 스냅샷 카탈로그, ADR 0031) | EOL 경과 한정 | 지원 종료 OS (Linux 11 distro + Windows Server build) |
| attention.agent_unstable | `get_attention_signals` | Redis `agent_restarts:{sid}` mget | 1h 슬라이딩 윈도우 | restart_count >= threshold |

설계 결정:
- `list_server_ids()`는 정수 PK만 fetch — `list_servers`(disks JSONB 등 11컬럼) 대비 페이로드 절감 (T8 패턴 동일 적용).
- partition pruning binding 통일: gap SQL의 `recent_hours`가 동적 binding (`(:recent_h * interval '1 hour')`) — service 인자와 SQL 결합을 SQL 본문 hardcode로 묵시화하지 않음 (#F3·#F9).
- 검색·온라인필터 사용 시 environment_overview·attention 자동 격리 — 라우터 `pages.py` 분기 (첫 페이지·검색 없음·필터 없음일 때만 노출).
- ViewModel·mapper 카탈로그: `docs/architecture/web/view-models.md` "대시보드 상단 요약" 절.
