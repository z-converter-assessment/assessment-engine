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
