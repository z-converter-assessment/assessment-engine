# Web 레이어

FastAPI 기반 SSR + JSON API 서버.

```
src/assessment_engine/web/
├── main.py                  — FastAPI 앱, lifespan, 라우터 등록, StaticFiles 마운트
├── deps.py                  — 의존성 주입 (composition root)
├── view_models.py           — Service → Router ViewModel (dataclass)
├── template_setup.py        — Jinja2Templates 단일 인스턴스 + filter 등록 격리
├── template_filters.py      — Jinja2 필터 함수 정의
├── routers/
│   ├── pages.py             — SSR 페이지 라우터 (/servers)
│   └── api.py               — JSON API 라우터 (/api/v1/servers)
├── services/
│   ├── query_service.py     — Redis + Repository 오케스트레이션
│   ├── mappers.py           — Outbound DTO → ViewModel 변환
│   ├── metrics_calculator.py — CPU/Mem/Disk/Net delta 계산
│   ├── cache_serializer.py  — Redis serde (ServerDetailResponse, MetricDashboard)
│   ├── units.py             — 단위 변환 유틸
│   ├── device_filters.py    — 물리 디스크·가상 마운트 필터
│   └── service_classifier.py — 서비스 카테고리 분류·포트 매핑
├── templates/               — Jinja2 SSR 템플릿
└── static/
    └── js/
        └── chart-utils.js   — 차트 템플릿 공통 유틸 (전역 ChartUtils)
```

---

## 레이어 원칙 (요약)

표시 계층 작업의 단일 진실은 **CLAUDE.md §E1 "렌더링 레이어 원칙" P1~P5**. 본 문서는 web 컴포넌트 구현 디테일에 집중하며 P 원칙의 전제가 된다. 충돌 시 CLAUDE.md가 우선.

- **P1 Repository raw만**: DB 쿼리 결과는 raw 단위(KB·bytes·jiffies). 변환·임계값 분류 금지.
- **P2 Service 단일 변환**: mapper에서 단위 변환·delta·dedup·정렬·badge_class·bar_color·is_well_known·cached_pct 모두 계산.
- **P3 Template 순수 렌더링**: 분기·반복·필터만. **계산·sort·임계값 비교·단위 변환 금지** — 정렬은 mapper의 `sorted_*` 필드를, 임계값은 mapper의 boolean/CSS 클래스를 사용.
- **P4 차트 JS 명시 예외**: 인터랙션 동적 시각화에 한해 JS에 그리드/라벨 연산 허용. (a)~(e) 5개 의무 규약.
- **P5 서버 단일 계산**: 클라이언트가 임계값/dedup/통계를 다시 계산하지 않음. 서버 ViewModel 또는 명명 상수 사용.

세부 책임:
- **Router**: HTTP 파라미터 추출, public_id → internal_id 변환, 404 처리, 템플릿/JSON 응답 반환. 표시 계층 셋업은 `template_setup.py`에 위임.
- **Service**: Redis 캐시 오케스트레이션, Repository 호출, DTO → ViewModel 변환. 검증은 라우터 Pydantic이 하므로 **중복 검증 금지**.
- **Repository**: DB 쿼리. 인터페이스(`BaseQueryRepository`)로만 접근. 구체 구현은 `deps.py`에서 주입.
- **Template**: ViewModel을 받아 표시만 담당. 분기·계산·변환 없음.

---

## 의존성 주입 (deps.py)

```python
def get_service(db: AsyncSession = Depends(get_db), redis: Redis = Depends(get_redis)) -> QueryService:
    return QueryService(QueryRepository(db), redis)
```

`QueryService`는 `BaseQueryRepository`(인터페이스)를 생성자에서 받는다. `deps.py`(composition root)에서 구체 구현체 `QueryRepository`를 생성해 주입.

`get_redis`는 `src/assessment_engine/db/redis.py`에서 직접 임포트. `get_redis_client()` 같은 래퍼 없음.

---

## URL 식별자

라우터의 `{server_id}` 경로 파라미터는 `public_id` (UUID 문자열). 정수 PK는 노출하지 않는다.

`QueryService.resolve_server_id(public_id) -> int | None` — UUID → 정수 PK 변환 브릿지. `pages.py` / `api.py` 공통 `_resolve()` 헬퍼에서 404 처리. `cache:resolve:{public_id}`로 read-through 캐싱 (TTL 없음 — public_id 불변).

---

## 서비스 계층 모듈

### query_service.py

Redis 캐시 read-through + Repository 호출 + ViewModel 변환 오케스트레이션.

캐시 키:

| 용도 | 키 | TTL · 무효화 |
|------|----|-----|
| public_id 조회 | `cache:resolve:{public_id}` | TTL 없음 (불변) |
| 인벤토리 | `cache:inventory:{server_id}` | 300s + consumer가 새 inventory 저장 시 즉시 DELETE |
| 메트릭 대시보드 | `cache:metrics:{server_id}` | 60s + consumer가 새 메트릭 저장 시 즉시 DELETE |

`list_servers`의 온라인 상태 조회는 N개 서버 직렬 `EXISTS` 호출 대신 `redis.mget([online:{id} for ...])` 한 번으로 묶는다 — 페이지당 라운드트립 N → 1.

`get_metric_chart`: 메트릭 타입 검증은 라우터 `Query(MetricType)` Literal Pydantic이 처리하므로 service 계층에서 **중복 검증 금지**. `fs.usage_percent`만 가상 마운트 필터, disk IOPS만 `device_category` 분류 적용.

SSE 스트림(`stream_metrics_events`): Redis `metrics.events` 채널 구독 → `server_id` 일치 메시지만 브라우저로 전달.

### mappers.py

Outbound DTO → ViewModel 변환. 모든 분기·필터링·파생 필드 계산이 여기에 집중된다.

`enrich_server_detail(detail)`: `ServerDetailResponse` 생성 후 또는 캐시 역직렬화 후 반드시 호출. display 전용 파생 필드 계산:
- `known_services`, `show_unknown_badge`, `key_listen_ports`
- `sorted_services` (unit ASC), `sorted_listen_ports` ((port, proto) ASC) — **템플릿 `| sort` 금지** 위함
- `os_display`, `cpu_display`, `disk_total_gb`

**임계값 단일화**: 모듈 상단 `_USAGE_DANGER_PCT = 90`, `_USAGE_WARN_PCT = 75`. `_usage_severity(pct) -> Literal["ok","warn","danger"]`이 분류, `_BADGE_CLASS_BY_SEVERITY`/`_BAR_COLOR_BY_SEVERITY` 매핑이 CSS 클래스·hex 색상 결정. 차트 JS의 동일 이름 상수(`USAGE_DANGER_PCT`/`USAGE_WARN_PCT`)와 동기화 — 변경 시 양쪽 갱신.

**raw dict → ViewModel 단일 진입점**: `_to_disk_item(d)`, `_to_listen_port_item(p)`, `_to_service_item(s, listen_ports)` 세 함수가 dict 키 매핑 책임을 가짐. `to_server_list_item`/`to_server_detail`/`enrich_server_detail` 모두 이들을 재사용 → 매핑 로직 중복 제거.

**`enrich_server_detail` idempotent**: 두 번 호출해도 결과 동일 (`cache_serializer.server_detail_from_json`이 역직렬화 후 재호출하는 시나리오 안전). `_DETAIL_DISPLAY_FIELDS` 셋으로 파생 필드 제거 후 재계산.

### metrics_calculator.py

**공통 helper**:
- `_group_by_dim(rows, key)` — device·interface 등 dimension별 시계열 그룹화. `compute_disk_io`/`compute_net_io` 양쪽 사용.
- `_delta_rate(cur, prev, dt)` — 누적 카운터의 시간당 변화율. `cur < prev` (counter reset) 시 None — "측정 불가" 표시.
- `_clip_to_remaining(raw_pct, remaining_room)` — stacked bar 누적용. used 위에 cached, 그 위에 buffers를 덧붙일 때 100% 초과 방지 (Linux available이 cached/buffers 일부 포함하므로 단순 합산 시 초과 가능).

snapshot 빌더: `_disk_io_snapshot`/`_net_io_snapshot` private — 페어 가드(len<2, dt<=0)와 rate 호출을 단일 위치에.

raw 누적값 → 계산된 스냅샷 변환.

- `compute_cpu`: 연속 2회 jiffies delta → CPU%
- `compute_mem`: 단일 시점 절댓값 + **stacked-bar 누적 비율(`cached_pct`/`buffers_pct`)도 계산** — 클라이언트가 `cached_kb / total * 100` 같은 계산을 다시 하지 않도록 (P5)
- `compute_swap`: 단일 시점 절댓값
- `compute_disk_io` / `compute_net_io`: 2회 delta + 경과 시간 → IOPS·kBps
- `compute_mounts`: 단일 시점 사용량, 가상 마운트 제외

delta < 0 (재부팅·에이전트 재시작 시 카운터 리셋) → `None` 처리 → UI에서 "—" 표시.

### cache_serializer.py

`ServerDetailResponse`와 `MetricDashboard`의 Redis serde.

`server_detail_from_json`: display 파생 필드(`_DETAIL_DISPLAY_FIELDS` set)를 data에서 제거 후 `ServerDetailResponse` 생성 → `enrich_server_detail()` 재계산. 새 파생 필드 추가 시 이 set도 갱신.

`ListenPortItem.is_well_known`은 옛 캐시 호환을 위해 `p.get("is_well_known", p.get("port", 0) <= 1024)` 폴백.

datetime 필드는 `datetime.fromisoformat()`으로 파싱 필수 (`json.loads`는 str 반환 → Jinja2 필터 오작동).

### units.py

| 함수 | 변환 |
|------|------|
| `bytes_to_gb(b)` | bytes → GB (round 2) |
| `kb_to_gb(kb)` | KB → GB (round 1) |
| `usage_pct(used, total)` | 사용률 % (round 1) |
| `sector_to_kbps(cur, prev, dt)` | 섹터 delta → kBps (512B/sector) |

### device_filters.py

에이전트가 필터링 없이 전송한 raw 데이터에서 가상·커널 항목을 제거하는 임시 필터.
근본 해결은 에이전트 측 수집 단계에서의 필터링 (다음 agent_version 계약 반영 예정).

- `is_physical_disk(name)`: `sd*|vd*|nvme*n*|mmcblk*` regex 매칭
- `is_lvm_disk(name)`: LVM 논리볼륨 — `dm-*` 패턴
- `is_partition(name)`: 파티션 — `sd*[0-9]|nvme*p*` 등 숫자/파티션 suffix 패턴
- `is_virtual_mount(fstype, mount)`: `proc/sysfs/devtmpfs/squashfs` 등 fstype 블록리스트 + `/proc /sys /dev/pts /snap` 경로 프리픽스 매칭

차트 API `device_category` 파라미터 처리:
- `phys`: `is_physical_disk`로 필터
- `logical`: LVM 우선(`is_lvm_disk`), 없으면 파티션 폴백(`is_partition`)
- 미지정 시 전체 반환

### service_classifier.py

`classify(unit) -> str`: 서비스명에서 `.service` suffix 제거 후 소문자 substring 매칭. 매칭 없으면 `"unknown"`.

`matched_ports(unit, listen_ports) -> list[MatchedPort]`: comm 기반 매칭 우선 → 없으면 `_SERVICE_PORTS` well-known 포트 테이블 폴백. `(proto, port)` 기준 dedup.

---

## ViewModel 설계

mapper에서 채워지는 파생 필드 정리. **템플릿이 임계값 비교·정렬·dedup을 못 하도록** 가능한 한 모두 계산해서 ViewModel에 둠.

### `ServiceItem(unit, sub, category, ports, display_name)`

- `display_name`: `unit.removesuffix(".service")` — list/detail mapper에서 채움.
- `ports`: detail mapper에서 `matched_ports()` 결과. list mapper는 `[]`.

### `ListenPortItem`

- `is_well_known`: `port <= 1024`. mapper(`to_server_detail`)에서 채움. **템플릿 `{% if p.port <= 1024 %}` 금지** 위함.

### `ServerListItem`

- `known_services`: category != "unknown" 서비스만, category 기준 dedup (동일 category는 첫 번째만).
- `show_unknown_badge`: services 있고 known_services 빈 배열일 때 True.
- `os_display`: `[os_id, os_version]` 공백 join.

list.html에서 뱃지는 `svc.category`만 표시 (display_name 없음). "마지막 수집" 컬럼 없음.

### `ServerDetailResponse` (`enrich_server_detail`에서 채움)

- `known_services`: 글로벌 dedup된 chips 포함.
- `show_unknown_badge`.
- `key_listen_ports`: `is_well_known` AND 서비스 매핑 포트 번호 제외, port·proto 정렬.
- `sorted_services`: unit ASC.
- `sorted_listen_ports`: (port, proto) ASC.
- `os_display`, `cpu_display`, `disk_total_gb`.

### `MountUsageItem` (`_build_mount_item`에서 채움)

- `badge_class`: usage_pct 임계값(90/75) 기반 CSS 클래스 (`badge-danger` / `badge-warn` / `badge-ok`).
- `bar_color`: 동일 임계값 기반 hex color.
- `device_name`: 마운트가 어느 물리 디스크 위에 있는지 (parent disk 이름). `device_filters.find_parent_disk(major, minor, disks)` 결과. 가상 마운트(major=0)나 매핑 실패 시 None → 템플릿이 "—" 표시.

### mount↔disk 매핑 (Linux 디바이스 식별 표준 활용)

inventory의 `disks[].major/minor`와 `mounts[].major/minor`(payload v3에 발행됨)를 (major, minor) 조인 키로 매칭. `src/assessment_engine/web/services/device_filters.find_parent_disk()`가 다음 규칙으로 매핑:

1. `mount.major == 0` 또는 (major, minor) 누락 → None (가상 파일시스템)
2. `mount.major == disk.major` 이면 같은 디스크 후보
3. `mount.minor == disk.minor` 이면 디스크 자체에 마운트
4. `0 < (mount.minor - disk.minor) < 16` 이면 그 디스크의 파티션 (SCSI/virtio 관례)

storage.html mounts 표의 "Device" 컬럼에 결과 노출. 이전의 정규식 휴리스틱(`is_physical_disk` name 기반)을 보강하지 않고 (major, minor) 직접 활용 — 이름 규칙 가정 의존 제거.

### `MemSnapshot` (`compute_mem`에서 채움)

- `cached_pct` / `buffers_pct`: stacked-bar 누적 비율. 클라이언트가 `cached_kb / total * 100`을 다시 계산하지 않도록 서버에서 미리 잘라냄 (P5).

### dataclass 필드 순서 주의

ViewModel dataclass에 default 있는 필드(`field(default_factory=...)`)와 default 없는 필드를 섞을 때 **default 없는 필드를 모두 위로** 올려야 한다. 그렇지 않으면 `non-default argument follows default` `TypeError` 발생. `ServerDetailResponse`에서 `services`/`listen_ports`(non-default) 뒤에 `sorted_services`/`sorted_listen_ports`(default factory) 배치.

---

## Jinja2 인프라

### template_setup.py — 단일 인스턴스
`Jinja2Templates` 인스턴스와 filter 등록을 `src/assessment_engine/web/template_setup.py`에 격리. 라우터(`pages.py`)는 `from web.template_setup import templates`만 한다 — 라우터에 표시 셋업 책임을 두지 않기 위함.

### template_filters.py — 필터 함수
`template_setup.py`에서 `templates.env.filters`에 등록.

| 필터 | 동작 |
|------|------|
| `kst` | datetime(UTC) → KST `"YYYY-MM-DD HH:MM:SS"`. None → `"-"` |
| `disksize` | float(GB) → `"1.2 TB"` / `"3.4 GB"`. None → `"-"` |
| `kbps` | float(kBps) → `"1.2 MBps"` / `"3.4 kBps"`. None → `"—"` |
| `service_badge_class` | category 문자열 → CSS 클래스명 (`badge-cat-web` 등) |
| `or_dash` | 값 → `str(값)`. None → `"-"` |

---

## 정적 자원 — `src/assessment_engine/web/static/js/chart-utils.js`

`src/assessment_engine/web/main.py`에서 `app.mount("/static", StaticFiles(directory=STATIC_DIR))`. `base.html` `<head>`에서 `/static/js/chart-utils.js` 단일 로드 → 전역 `ChartUtils`. (자식 template의 `<main>` 안 inline script가 `ChartUtils`를 즉시 destructure하므로 head에서 먼저 로드되어야 함)

| 항목 | 제공 |
|------|------|
| 상수 | `RANGE_LABEL` / `AUTO_BUCKET` / `BUCKET_LABEL` / `RANGE_MS` / `BUCKET_MS` / `COLORS` |
| 시간 포매팅 | `fmtKst(iso)` / `fmtLabel(ts, range)` |
| 처리량 포매팅 | `fmtKbChart(v)` (B/s ↔ kB/s ↔ MB/s) |
| anchor 입력 | `getAnchorEnd(inputId)` / `initAnchor(inputId)` |
| 버킷 그리드 | `makeBucketGrid(rangeKey, bucketKey, anchorEnd)` / `joinToGrid(grid, rows, bMs)` |
| 토글 그룹 | `bindToggle(groupId, onChange)` |
| SSE | `initSse(serverId, onMessage)` — dot/label 자동 갱신 + 재연결 메시지 |
| 응답 방어 | `safeArray(arr)` |

각 차트 템플릿은 상단에서 `const { ... } = ChartUtils;`로 destructure. **인라인 중복 정의 금지**. wrapper로 전역 state(예: `globalRange`)를 캡처해야 하는 경우만 짧은 어댑터 함수 (`const fmtLabel = iso => ChartUtils.fmtLabel(iso, globalRange);`).

---

## asyncpg 파라미터 주의사항

(repository 구현체 함정. db.md와 중복 — 표시 계층은 raw SQL 안 짜므로 web 작업 시에는 service 계층 신경 쓰면 됨.)

1. **interval 산술**: `collected_at >= :start - interval '5 minutes'` — asyncpg가 `:start` 타입을 추론하지 못함.
   → Python에서 `window_start = start - timedelta(minutes=5)` 계산 후 파라미터로 전달.

2. **named parameter + PostgreSQL cast**: `:dim::text` 형태는 SQLAlchemy가 `::` 뒤를 파싱하지 못해 `:dim` 그대로 남음.
   → `CAST(:dim AS text)` 로 대체.

---

## 템플릿 차트 UI 설계

Chart.js 4.4.3 사용.

### 뱃지(badge) CSS 규격
전 템플릿 통일: `padding: 4px 10px; border-radius: 6px; font-size: 12px`.

### 비동기 차트 로더 표준 템플릿 (P4 의무 규약)

5개 규약 (sequence counter / capture-before-await / `Array.isArray` / 404 분기 / suggestedMax 명명 상수)을 동시 적용. CLAUDE.md §E9 참조.

```javascript
const { RANGE_LABEL, AUTO_BUCKET, BUCKET_LABEL, BUCKET_MS, COLORS,
        fmtLabel, getAnchorEnd, initAnchor, makeBucketGrid, joinToGrid,
        bindToggle, initSse, safeArray } = ChartUtils;

let xxxSeq = 0;
async function loadXxxChart() {
  const seq = ++xxxSeq;                               // (a)
  const capturedRange  = xxxRange;                    // (b)
  const capturedAnchor = getAnchorEnd('xxx-anchor');
  try {
    const rows = await fetch(`/api/...`).then(r => r.json());
    if (seq !== xxxSeq) return;                       // (a)
    const safe = safeArray(rows);                     // (c)
    renderXxxChart(safe, capturedRange, capturedAnchor);
  } catch(e) { console.error(e); }
}

async function loadSnapshot() {                       // (d) 404 분기
  const res = await fetch(`/api/v1/servers/${SERVER_ID}/metrics/latest`);
  if (res.status === 404) { showEmpty(); return; }
  if (!res.ok) return;
  renderSnapshot(await res.json());
}

initSse(SERVER_ID, loadSnapshot);
```

### avg+max 음영 패턴

avg 데이터셋(짝수 인덱스)과 max ghost 데이터셋(홀수 인덱스)을 쌍으로 구성.

- avg: `fill: '+1'`, `pointRadius: 1`, `pointHoverRadius: 3`, 실선
- max ghost: `borderColor:'transparent'`, `backgroundColor:'transparent'`, `pointRadius: 0`, `pointHoverRadius: 0`
- `bufferedMaxData`: avg가 null인 버킷은 max도 null — 빈 구간 음영 방지
- 실제 max 값은 `realData` 커스텀 속성에 보관 → 툴팁에서 `ds.realData[idx]`로 참조
- 툴팁 filter: `item.datasetIndex % 2 === 0` — avg 데이터셋만 표시

### Y축 / 색상 명명 상수 (P4 (e))

스크립트 상단에 분리. 변경 시 의도 추적 가능.

```javascript
// 처리량/IOPS 기준선 — soft ceiling
const NET_Y_SUGGESTED_MAX     = 2048;              // B/s
const PERF_IOPS_SUGGESTED_MAX = 200;               // HDD 랜덤 I/O 한계
const PERF_NET_SUGGESTED_MAX  = 10 * 1024 * 1024;  // 10 MB/s — 1 Gbps의 8%

// 색상 임계값 — 서버 mappers._usage_bar_color 와 동일 기준. 변경 시 양쪽 동기화.
const USAGE_DANGER_PCT = 90;
const USAGE_WARN_PCT   = 75;
const SWAP_DANGER_PCT  = 0.1;
const COLOR_OK = '#3b82f6'; const COLOR_WARN = '#f59e0b';
const COLOR_DANGER = '#ef4444'; const COLOR_NEUTRAL = '#64748b';
```

| 차트 | 설정 |
|------|------|
| 네트워크 I/O 추이 | B/s 원값. `fmtKbChart` 동적 포매터. `suggestedMax: NET_Y_SUGGESTED_MAX` |
| CPU·메모리 사용률 | `min:0, max:100` 고정 |
| 스왑 사용률 | `beginAtZero: true, suggestedMax: 25` (낮은 값도 시각 부각) |
| IOPS 추이 | `ticks: { precision: 0 }` — 정수 눈금만 |
| 성능 IOPS | `suggestedMax: PERF_IOPS_SUGGESTED_MAX` |
| 성능 네트워크 | `suggestedMax: PERF_NET_SUGGESTED_MAX` |
| Load 15m | `suggestedMax: cpu_cores` |

### SSE 상태 + 수집기준시간 레이아웃

SSE dot/label과 수집기준시간 span은 반드시 **단일 flex 컨테이너** 안에 두어 줄바꿈을 방지한다.

```html
<div id="sse-status" class="no-print"
     style="display:flex; align-items:center; gap:5px; font-size:11px; color:#94a3b8; white-space:nowrap;">
  <span id="sse-dot" class="dot dot-off"></span>
  <span id="sse-label">연결 중...</span>
  <span id="xxx-snapshot-ts" style="margin-left:4px;"></span>
</div>
```

---

## 설계 결정

### SSR + AJAX 하이브리드

페이지 초기 렌더링은 SSR(Jinja2), 메트릭 수치는 AJAX. SSE 기반 실시간 갱신은 AJAX 패턴이어야 자연스럽다. 인벤토리 정적 정보는 SSR로 빠르게 표시하고, 메트릭은 비동기로 채워 사용자 체감 속도와 실시간성을 모두 확보한다.

### SSE 브로드캐스트: 전체 구독 후 서버별 필터링

`metrics.events` 채널을 모든 SSE 연결이 구독하고, 서버 측에서 `server_id` 일치 여부로 필터링. B2B 내부 포털 특성상 동시 접속자·서버 수가 적으므로 단일 채널 구조가 단순하고 Redis 채널 관리 오버헤드가 없다.

개선 방향(P2): 채널을 `metrics.events.{server_id}`로 분리. `docs/tradeoffs.md` T5.

### 캐시 전략: read-through + consumer DEL

consumer가 새 메트릭 저장 후 캐시를 직접 갱신하지 않고 `DEL cache:metrics:{id}`만 수행. 다음 Web 요청에서 캐시 미스 → DB 조회 → SET. write-through라면 consumer가 Web 직렬화 로직을 알아야 하므로 계층 결합도가 높아진다. SSE 이벤트 수신 후 브라우저가 AJAX를 한 번만 재요청하므로 캐시 미스는 메트릭 저장 직후 1회뿐이다. cache-aside race 한계는 `docs/tradeoffs.md` T2.

### counter reset: delta < 0 → None

재부팅·에이전트 재시작 시 카운터가 0으로 리셋되어 delta가 음수가 된다. `None` 처리 후 UI에서 "—" 표시. 잘못된 수치를 보여주는 것보다 낫다.

개선 방향(P2): 두 시점의 `boot_time`이 다르면 delta 계산을 건너뛰는 로직을 `metrics_calculator.py`에 추가.

### chart-utils.js 추출 vs 인라인

5개 차트 템플릿이 `fmtKst` / `bindToggle` / `COLORS` / `AUTO_BUCKET` / `BUCKET_MS` / `makeBucketGrid` / `joinToGrid` / `fmtLabel` / SSE 초기화 등을 동일하게 정의하던 중복을 `static/js/chart-utils.js`로 추출. `StaticFiles` 마운트 + `base.html` script 1회 로드 → 전역 `ChartUtils`로 destructure. 트레이드오프(번들 도구 미도입 대신 단순한 IIFE 노출)는 `docs/tradeoffs.md` T9.

---

## 운영 / 디버깅

### web 로그

```bash
docker compose logs -f web                          # 실시간 (uvicorn reload 메시지 포함)
docker compose logs web --since=10m                 # 최근 10분
docker compose logs web 2>&1 | grep -E "ERROR|TRACE|Traceback"
```

uvicorn reload 모드라 코드 변경 시 `WatchFiles detected changes ... Reloading` 로그가 뜨고 자동 재기동.

### lifespan 실패 (web unhealthy)

```bash
docker compose ps web                                # STATUS unhealthy 확인
docker compose logs web --tail=50                    # 마지막 50줄
```

가장 흔한 lifespan 실패:
- ORM dataclass 필드 순서 위반: `TypeError: non-default argument 'X' follows default argument`
- TimescaleDB 확장 미설치: `extension "timescaledb" does not exist` — `image:` 확인
- `create_hypertable` 실패: 기존 테이블이 이미 있는데 hypertable로 변환 안 된 경우 — `down -v` 후 재기동

### HTTP 검증

```bash
PUB=$(docker compose exec -T postgres psql -U assessment -d assessment -tAc "SELECT public_id FROM server_inventory LIMIT 1;" | tr -d ' ')

# 페이지
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/health
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/servers/
curl -s -o /dev/null -w "%{http_code}\n" "http://localhost:8000/servers/$PUB/cpu"

# API
curl -s "http://localhost:8000/api/v1/servers/$PUB/metrics/latest" | jq .
curl -s "http://localhost:8000/api/v1/servers/$PUB/metrics/chart?metric_type=cpu.usage_percent&time_range=15m&bucket=1m&agg=avg" | jq .

# SSE (수동 종료 필요)
curl -N "http://localhost:8000/api/v1/servers/$PUB/metrics/stream"

# 정적 자원
curl -s -I http://localhost:8000/static/js/chart-utils.js
```

### 캐시 무효화 수동

```bash
# 인벤토리/메트릭 캐시 강제 삭제 후 다음 요청에서 DB 재조회
docker compose exec redis redis-cli DEL "cache:inventory:1" "cache:metrics:1" "cache:resolve:<public_id>"
```

### 차트 JS 디버깅

브라우저 DevTools → Console 활성화 후 차트 페이지 로드.
- `console.error`로 fetch 실패 / JSON 파싱 실패 확인.
- Network 탭에서 `/api/v1/servers/{id}/metrics/chart?...` 응답 확인.
- `window.ChartUtils`가 정의되어 있는지 확인 (StaticFiles 정상 로드).
- range 토글 빠르게 클릭 → sequence counter 동작 확인 (이전 응답이 그래프를 덮어쓰지 않아야 정상).

### 흔한 트러블

| 증상 | 원인 | 해결 |
|------|------|------|
| 메트릭 페이지에 "수집된 메트릭이 없습니다" | 인벤토리 1건 + metrics 1건만 있어 delta 계산 불가 (2회 필요) | 60초 더 기다리기 |
| 차트가 비어있음 (데이터 있음에도) | `/metrics/chart` 응답이 `[]` — time_range 안에 데이터 없음 | range 더 길게 |
| range 토글 후 차트가 옛 데이터로 잠깐 깜빡임 | sequence counter 누락 | 해당 차트 로더에 P4 (a) 적용 |
| 차트 색상이 명세와 다름 | `USAGE_DANGER_PCT` 같은 명명 상수 변경 누락 | mapper의 `_usage_bar_color`와 동기화 |
| `/static/js/chart-utils.js` 404 | StaticFiles 마운트 누락 또는 경로 오타 | `src/assessment_engine/web/main.py`의 `app.mount("/static", ...)` 확인 |
| 캐시 역직렬화 후 `KeyError` | ViewModel 필드 추가 후 `_DETAIL_DISPLAY_FIELDS` set 갱신 누락 | `cache_serializer.py` 확인 + `down -v` 또는 `DEL cache:*` |