# Web 서비스 계층 모듈

`web/services/` 하위 모듈별 책임. P2 — 모든 표시 파생은 mapper 단일 변환.

| 모듈 | 책임 |
|------|------|
| `query_service.py` | Redis 캐시 + repository 오케스트레이션. SSR/JSON 양 경로에 일관된 ViewModel·Summary 반환 |
| `task_service.py` | Task 발행 (DB INSERT + Redis SET). 트랜잭션 경계 + `IntegrityError` → `_DuplicatePending` 변환 |
| `recommendation.py` | USE Method 분류 (idle/shutdown/over/under/optimal). 임계값 출처는 `ai_roadmap.md §3.C` |
| `mappers.py` | Outbound DTO + Detail → ViewModel 변환. `to_*_item` / `enrich_*` / `infer_role` / `_split_disks` 등 |
| `metrics_calculator.py` | CPU/Disk/Net delta + Mem/Swap 시점값 → Snapshot. `_is_counter_reset` (boot_time 비교) |
| `cache_serializer.py` | Redis serde — `ServerDetailResponse` / `MetricDashboard`. 역직렬화 후 `enrich_*` 재호출 (idempotent) |
| `units.py` | KB→GB · sectors→KB/s · usage_pct 단위 변환 |
| `device_filters.py` | 물리 디스크·LVM·partition 분류 + 가상 마운트 필터 + `find_parent_disk` (mount↔disk 조인) |
| `service_classifier.py` | systemd unit → 서비스 카테고리 (`web`/`db`/`cache`/`mq`/`monitor` 등) + 포트 매핑 |

## 서비스 분류 — 3단계 표시 계층

| 단계 | 페이지 | 표시 |
|------|--------|------|
| 목록 | `/servers/` | 카테고리 badge만 (`web`/`db`/...) — 원본 unit 노출 안 함 |
| 상세 | `/servers/{id}` | unit 이름 + matched_ports + 카테고리 badge |
| services 탭 | `/servers/{id}/services` | unit 전체 + sub state + 포트 + 카테고리 |

`service_classifier.classify(unit)` — keyword substring 매칭. 분류 실패 시 `"unknown"`. role 추론(`infer_role`)도 같은 함수 활용 — 가장 빈도 높은 카테고리.

## mount ↔ disk 매핑 (Linux 디바이스 식별 표준)

`device_filters.find_parent_disk(mount_major, mount_minor, disks)`:
- `mount.major == disk.major AND mount.minor == disk.minor` → 디스크 자체 마운트
- `disk.minor + 1..15` → 그 디스크의 partition (SCSI/virtio 관례)
- 가상 (major=0, tmpfs) → None

storage 페이지 mount → disk 매칭 + `_split_disks` (Inventory JSON Export의 `additional_disks.mount_hint`)에 활용.

## Recommendation 분류 — USE Method 출처

`recommendation.py` 임계값은 모두 출처 주석 명시:
- AWS Compute Optimizer: idle (CPU peak ≤1%) / over (CPU p95 ≤30%, MEM p95 ≤50%)
- Azure Advisor: shutdown (CPU p95 ≤3%, NET ≤2Mbps)
- GCP Recommender: headroom 30%
- Kleinrock 큐잉 이론(1975): under (CPU p95 ≥70%)
- Linux page cache: under (MEM p95 ≥80%)

UI badge 임계값(`mappers._USAGE_DANGER_PCT=90`/`_USAGE_WARN_PCT=75`)과는 별 도메인 — 시점 사용량 시각 신호 vs 14일 통계 right-sizing 결정.

## 목록 화면 상단 요약 — risk_top + attention

`/servers/` 첫 페이지에서 두 시선의 "주의 필요" 신호를 표시. 시간 축·도메인 차별 (#E1 P5 — 동일 데이터 한 번만).

| 시선 | service 메서드 | repo SQL | 시간 축 | 분류 |
|------|----------------|----------|---------|------|
| risk_top | `get_risk_top(limit=3)` | `list_server_ids` + `report_aggregate(period_days=1)` | 24h USE 통계 (만성) | 오프라인·스왑·MEM·CPU 우선순위 |
| attention.disk_warnings | `get_attention_signals` | `disk_usage_warnings(threshold_pct=85)` 단일 SQL | 7d 안 mount latest (현재) | 사용률 ≥85% |
| attention.gap_warnings | `get_attention_signals` | `metric_gap_warnings(gap_min=5, recent_h=24)` 단일 SQL | 5min~24h 갭 (단기) | "한때 살아있다 끊김" |

설계 결정:
- risk_top은 `report_aggregate` 재사용 — 새 SQL 안 만듦. service에서 score 계산 + sort.
- `list_server_ids()`는 정수 PK만 fetch — `list_servers`(disks JSONB 등 11컬럼) 대비 페이로드 절감 (T8 패턴 동일 적용).
- partition pruning binding 통일: gap SQL의 `recent_hours`가 동적 binding (`(:recent_h * interval '1 hour')`) — service 인자와 SQL 결합을 SQL 본문 hardcode로 묵시화하지 않음 (#F3·#F13).
- 검색·온라인필터 사용 시 두 섹션 자동 격리 — 라우터 `pages.py` 분기.
- ViewModel·mapper 카탈로그: `docs/architecture/web/view-models.md` "목록 화면 상단 요약" 절.
