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
| `device_filters.py` | 디스크/인터페이스 블랙리스트 필터 (`is_physical_disk`·`is_virtual_disk`·`is_lvm_disk`·`is_partition`·`is_virtual_interface`) + 가상 마운트 필터 + `find_parent_disk` (mount-disk 조인) |
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

## 디바이스 필터 정책 — 블랙리스트 (관측성 놓침 방지)

디스크·네트워크 인터페이스는 블랙리스트로 거른다 — 명백한 가상·시스템 디바이스만 제외하고 나머지는 통과. 화이트리스트(알려진 패턴만 허용)는 특이 물리 디바이스(`mpath`·`cciss`·새 NIC naming)를 조용히 놓치는 위험이 있어, 관측성에선 "놓침이 노이즈보다 치명적" 원칙으로 블랙리스트가 정석 (node_exporter `device-exclude` 등 de facto). 보안의 default-deny(화이트)와 반대 방향임에 주의 — 관측성은 "모르는 것도 일단 보여야" 안전.

- `is_physical_disk(name)` = `not (is_virtual_disk OR is_lvm_disk OR is_partition)`. 가상(`loop`/`ram`/`zram`/`fd`/`sr`/`nbd`)·논리(LVM/RAID `dm-`/`md`)·파티션 제외, 나머지(sd/vd/nvme/mmcblk/PhysicalDrive + 특이 컨트롤러) 통과. (과거 화이트리스트 `_PHYS_DISK_RE`에서 전환.)
- `is_virtual_interface(name)` = 보수적 1번 범위만 제외 — `lo`·터널(`sit`/`tunl`/`ip6tnl`/`gre`/`gretap`/`erspan`)·`veth`·`dummy`·`ifb`·`nlmon` + Windows NDIS 필터 드라이버(`-NNNN` suffix). `docker`/`br-`/`bond`/`vlan` 회색지대는 통과(컨테이너·본딩 호스트 정보 손실 방지).

적용 경계 — 저장은 모두 유지, 표시 경계에서만 필터:
- 디스크: `compute_disk_io`(스냅샷) + `to_storage_detail`(인벤토리 물리 디스크) + `query_service._filter_disk_category`(차트 `device_category=phys`).
- 인터페이스: `compute_net_io`(스냅샷) + `query_service.get_metric_chart`(차트 `_NET_METRIC_TYPES`).

IP 필터 보류: `ip_internal`/`ip_external`은 평면 IP 목록만 발행돼(인터페이스 매핑 부재) 가상/물리 구분이 주소 형식만으론 불가 — docker 사설 IP와 물리 사설 IP가 같은 대역(192.168/10/172.16/fd00 ULA). 링크로컬(`fe80::/10`·`169.254/16`)·루프백 정도만 형식 필터 가능하나 이득이 작아, agent가 IP-인터페이스 매핑을 발행하기 전까지 IP는 필터하지 않는다 (인터페이스 IO 필터는 device 이름이 명확해 유지).

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
