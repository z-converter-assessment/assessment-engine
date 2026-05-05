# 설계 결정 및 개선 계획

의도적으로 선택한 트레이드오프와 향후 개선 방향을 함께 기록한다.
각 항목은 **현재 설계 → 수용 근거 → 개선 방향** 순으로 구성된다.

---

## 1. 멱등성: at-most-once

**현재 설계**
Redis `SET NX`로 중복 제거 키를 설정한 뒤 DB에 커밋한다.
```
SET idempotent:{message_id} NX → DB 커밋
```
SET NX 이후 DB 커밋 이전에 프로세스가 죽으면, RabbitMQ 재전송 메시지가 중복으로 판단되어 조용히 드롭된다. **데이터 유실(at-most-once)** 가능성이 있다.

순서를 뒤집어 DB 커밋 후 SET NX를 하면 at-least-once가 된다. 같은 메트릭이 두 번 삽입되면 delta 계산이 오염되어 CPU/IOPS/kBps 수치가 왜곡된다. 메트릭 특성상 중복 삽입이 데이터 유실보다 더 해롭다.

**수용 근거**
메트릭은 60초마다 재발행되므로 한 샘플 유실은 차트에서 짧은 gap으로 표시될 뿐이다.
프로세스가 SET NX와 DB 커밋 사이에 정확히 죽는 확률은 매우 낮다.

**개선 방향 (P3)**
`(server_id, collected_at)` 복합 UNIQUE 제약을 추가하면 DB 레벨에서 중복 삽입을 막을 수 있다.
이 경우 순서를 DB 커밋 → SET NX로 바꿔 at-least-once로 전환 가능하다.

---

## 2. RabbitMQ 큐 TTL: 메시지 유형별 차등

**현재 설계**
| 큐 | TTL |
|----|-----|
| `server.inventory` | 없음 |
| `server.metrics` | 60초 |
| `server.error` | 60초 |

metrics/error에 60초 TTL을 유지하면 컨슈머 다운 중 쌓인 메트릭은 전부 DLQ로 빠진다. 수집 공백이 발생하지만, 낡은 메트릭을 뒤늦게 삽입하면 시계열 연속성이 깨지는 부작용이 더 크다.

**수용 근거**
inventory는 기동 시 1회만 발행되는 one-shot 메시지다. TTL 소실 시 에이전트 재시작 전까지 복구 방법이 없다.
metrics는 주기적으로 재발행되므로 컨슈머 복구 후 자연스럽게 재개된다.

---

## 3. Redis: 메시지 처리 critical path 포함

**현재 설계**
멱등성 체크(`SET NX`)가 RabbitMQ 메시지 처리 흐름 안에 있다. Redis 장애 시 예외가 발생하고 메시지는 nack → DLX → DLQ로 이동한다.

fail-open(Redis 장애 시 체크 생략 후 처리 계속)으로 바꾸면 Redis 장애 중에도 메시지는 처리되지만, 동일 메시지 중복 처리 가능성이 생긴다.

**수용 근거**
Redis는 캐시, PUB/SUB, 온라인 TTL로 이미 hard dependency다. Redis 장애 시 웹 UI 자체가 정상 동작하지 않으므로, 멱등성 때문에 새로운 단일 장애점이 추가되는 구조가 아니다.

---

## 4. 미등록 서버의 메트릭 드롭

**현재 설계**
metrics 메시지 수신 시 `machine_id`로 서버를 조회하고, 미등록이면 메트릭을 조용히 드롭한다.

inventory 메시지가 처리되기 전에 metrics 메시지가 도달하면(레이스 컨디션, 또는 inventory가 DLQ로 빠진 경우) 메트릭이 유실된다.

**수용 근거**
에이전트가 계속 메트릭을 발행하므로 inventory 등록 후 다음 주기(최대 60초)부터 자동으로 수집된다.
미등록 서버의 메트릭을 임시 저장하는 구조는 고아 데이터 관리 복잡도를 높인다.

---

## 5. counter reset 처리: delta < 0 → None

**현재 설계**
두 시점의 raw 누적값 차(delta)가 음수이면 `None`으로 처리하고 UI에서 "—"로 표시한다.
에이전트 재시작·재부팅 시 카운터가 0으로 리셋되어 delta가 음수가 된다.

**수용 근거**
재부팅·재시작은 드문 이벤트고, 해당 구간만 "—"로 표시되는 것이 잘못된 수치를 보여주는 것보다 낫다.

**개선 방향 (P2)**
`server_metrics`에 `boot_time` 컬럼을 추가하거나, 두 시점의 `boot_time`이 다르면 delta 계산을 건너뛰는 로직을 `MetricsCalculator`에 추가한다. 카운터 리셋 시점을 정확하게 감지해 해당 샘플만 건너뛸 수 있다.

---

## 6. 차트 dimension 필터: CAST(:dim AS text) IS NULL 패턴

**현재 설계**
dimension 파라미터가 None일 때 전체 조회, 값이 있으면 해당 dimension만 필터링하는 단일 쿼리를 유지한다.
```sql
AND (CAST(:dim AS text) IS NULL OR device = :dim)
```
dimension 유무에 따라 쿼리를 분기하면 각 쿼리가 더 단순해지고 인덱스 플래너가 최적화할 여지가 생기지만, 코드 중복이 발생한다.

**수용 근거**
현 데이터 규모(서버 수십 대)에서는 성능 차이가 없다.
`:dim::text IS NULL` 형태는 SQLAlchemy + asyncpg에서 named parameter 뒤 `::` 파싱 버그가 있으므로 `CAST(:dim AS text)`로 우회했다.

---

## 7. SSE 브로드캐스트: 전체 구독 후 서버별 필터링

**현재 설계**
`metrics.events` Redis 채널을 모든 SSE 연결이 구독하고, 브라우저로 전달할 이벤트는 서버 측에서 `server_id` 일치 여부로 필터링한다.

서버 수와 동시 접속자 수가 늘어나면, 각 SSE 연결이 관련 없는 이벤트를 수신·버리는 비율이 높아진다.

**수용 근거**
B2B 내부 포털 특성상 동시 접속자 수가 적고, 서버 수도 수백 대 이하로 예상된다. 단일 채널 구조가 단순하고 Redis 채널 관리 오버헤드가 없다.

**개선 방향 (P2)**
채널을 `metrics.events.{server_id}`로 분리한다. 각 SSE 연결이 해당 서버의 채널만 구독하고, consumer는 `redis.publish(f"metrics.events.{server_id}", ...)`로 발행한다.

---

## 8. DEV 환경 스키마 관리: web lifespan → consumer depends_on

**현재 설계**
web 컨테이너 lifespan에서 `CREATE EXTENSION + create_all + create_hypertable`을 수행하고, consumer가 `depends_on web: condition: service_healthy`로 web 헬스체크 통과 후 기동한다.

web 재시작마다 lifespan이 실행되고, 프로덕션에서는 web과 DB 마이그레이션 책임이 뒤섞이는 구조가 된다.

**수용 근거**
프로토타입~초기 개발 단계에서 별도 마이그레이션 도구 없이 빠르게 스키마를 유지할 수 있다. `create_all`은 이미 존재하는 테이블을 건드리지 않으므로 데이터 유실 위험은 없다.

**개선 방향 (P1)**
- Alembic 초기화 및 `env.py` 설정
- 현재 스키마를 초기 마이그레이션으로 작성 (`create_hypertable` 포함)
- `consumer depends_on web: service_healthy` 제거 → consumer가 DB에 직접 의존

---

## 9. 캐시 전략: read-through + consumer DEL

**현재 설계**
consumer가 새 메트릭을 저장한 후 캐시를 직접 갱신하지 않고 `DEL cache:metrics:{id}`만 수행한다. 다음 Web 요청 때 캐시 미스 → DB 조회 → 캐시 SET이 일어난다.

write-through라면 저장 직후 첫 요청도 캐시를 타지만, consumer가 Web 서비스 계층의 직렬화 로직을 알아야 하므로 계층 결합도가 높아진다.

**수용 근거**
메트릭은 60초 주기로 갱신되고, SSE 이벤트 수신 후 브라우저가 AJAX를 한 번만 재요청하므로 캐시 미스는 메트릭 저장 직후 1회뿐이다. 계층 분리의 이점이 이 비용보다 크다.

**개선 방향 (P2)**
캐시 TTL이 `query_service.py`에 하드코딩되어 있다. `config.py`에 통합해 `.env`로 런타임 조정 가능하도록 한다.
```python
# config.py
redis_ttl_cache_inventory: int = 300
redis_ttl_cache_metrics: int = 60
```

---

## 10. SSR + AJAX 하이브리드

**현재 설계**
페이지 초기 렌더링은 SSR(Jinja2)로 제공하고, 메트릭 수치(CPU%·mem% 등)는 별도 AJAX 요청으로 로드한다.

Full SSR이면 매 페이지 요청마다 느린 메트릭 집계 쿼리를 블로킹으로 처리해야 한다. SPA면 초기 렌더링이 느리고 클라이언트 라우팅 구현이 필요하다. 하이브리드는 SSR 페이지와 API 엔드포인트를 이중으로 유지해야 한다.

**수용 근거**
SSE 기반 실시간 갱신은 AJAX 패턴이어야 자연스럽게 구현된다. 인벤토리 정적 정보는 SSR로 빠르게 표시하고, 메트릭은 비동기로 채워 넣는 구조가 사용자 체감 속도와 실시간성을 모두 확보한다.

---

## 11. inventory 수신 시 online 즉시 마킹

**현재 설계**
`make_inventory_handler()`에서 upsert 성공 후 `SET online:{server_id} EX 90`을 수행한다. 실제 메트릭이 아직 수신되지 않은 상태에서도 서버가 온라인으로 표시된다.

첫 메트릭 수신까지 최대 60초가 걸리므로, 그 사이 온라인 뱃지가 "에이전트가 살아있다"는 의미보다 "등록됐다"는 의미로 오해될 수 있다.

**수용 근거**
inventory를 발행한 에이전트는 직후 60초 안에 metrics를 발행하므로 온라인 상태 오표시 구간이 짧다. 등록 즉시 피드백을 주는 것이 UX상 더 낫다.

---

## 12. InventoryMountInfo 미사용 필드

**현재 설계**
`consumer/schemas.py`의 `InventoryMountInfo`에 `free_bytes`, `avail_bytes` 필드가 정의되어 있으나, `handler.py`에서 인벤토리 저장 시 이 필드들은 무시된다. 동적 사용량은 `server_mount_usage`에 별도 저장한다.

**개선 방향 (P3)**
`handler.py`의 `_to_inventory_create()` 내부에 의도를 명시하거나, `InventoryMountInfo`에 필드 수준 docstring을 추가해 혼란을 방지한다.

---

## 14. 물리 디스크 필터링: 포털 임시 처리 + 에이전트 계약

**개념 정리**

_디스크(스토리지)_: 물리적 저장 장치 자체. `sda`, `nvme0n1` 같은 블록 디바이스. 용량·읽기/쓰기 속도 같은 하드웨어 속성을 가진다.

_마운트 포인트_: 디바이스(또는 파티션)를 특정 파일 경로에 연결한 지점. `/dev/sda1 → /` 처럼 "이 디바이스를 이 경로에서 접근 가능하게 매핑"하는 것.

```
물리 디바이스 (sda)
  └─ 파티션 (sda1)
       └─ 마운트 포인트 (/) ← 여기서부터 파일 경로로 접근 가능
```

_커널 가상 마운트_: 실제 저장 장치가 없는 마운트. 디스크에 데이터를 저장하는 게 목적이 아니라, 커널이 내부 상태를 파일 인터페이스로 노출하거나 프로세스 간 통신을 위해 만든 메모리 기반 파일시스템이다. 리눅스 "모든 것은 파일이다" 철학에 따라 커널 API를 파일 경로로 노출하는 수단이므로, 스토리지 용량·사용률 평가 대상이 아니다.

| 마운트 포인트 | fstype | 역할 |
|---------------|--------|------|
| `/proc` | procfs | 실행 중인 프로세스 정보 (`/proc/diskstats` 등) |
| `/sys` | sysfs | 커널 오브젝트(디바이스·드라이버) 트리 |
| `/dev` | devtmpfs | 블록/캐릭터 디바이스 파일 |
| `/run` | tmpfs | 부팅 후 런타임 임시 파일 (재부팅 시 초기화) |
| `/sys/fs/cgroup` | cgroup2 | 프로세스 리소스 제한 |
| `/snap/xxx` | squashfs | snap 패키지 (loop 디바이스 경유, 읽기 전용) |

_Snap과 loop 디바이스_: Snap 패키지는 SquashFS 압축 이미지(`.snap` 파일)로 배포된다. 이 파일을 실행하려면 loop 디바이스를 통해 마운트해야 하므로, snap 패키지 1개당 loop 디바이스 1개(`/dev/loop0`, `/dev/loop1`, ...)가 생성된다. Ubuntu 환경에서 snap이 많을수록 `/proc/diskstats`에 loop 디바이스가 그 수만큼 나타난다.

**현재 설계**
에이전트가 `/sys/block/` 또는 `/proc/diskstats`를 스캔할 때 물리·가상 디바이스를 구분하지 않고 전부 `disks[]`에 담아 전송한다. Ubuntu + snapd 환경에서는 snap 패키지 수만큼 `loop0`, `loop1`, ... 디바이스가 인벤토리에 포함된다.

포털 `mappers.py`의 `_is_physical_disk()`에서 allowlist regex(`sd*|vd*|nvme*|mmcblk*` 등)로 임시 필터링한다.

**loop I/O 이중 집계 문제**
loop 디바이스 I/O는 sda(또는 dm-0)에도 이미 반영된다.
```
앱 read → loop0(squashfs) → /var/lib/snapd/snaps/*.snap 파일 → sda
```
`/proc/diskstats`에서 loop0과 sda 양쪽에 I/O가 계상되므로, loop를 포함하면 이중 집계가 된다.
I/O 차트는 `IS_PHYS` regex로 이미 loop를 제외하고 있다.

**수용 근거**
에이전트는 C99/C++03으로 고객사 환경에 배포되어 있어 포털보다 업데이트 주기가 길다. 임시 필터로 즉시 노이즈를 제거하고, 에이전트 다음 버전에서 근본 해결한다.

**에이전트 계약 변경 사항 (다음 agent_version 협의 필요)**

`disks[]` 계약:
- **물리 스토리지 디바이스만** 포함한다.
- 수집 제외: `loop*`(snap/ISO 마운트), `ram*`(RAM disk), `zram*`(압축 RAM), `sr*`(광학 드라이브).
- 수집 포함: `sd*`, `vd*`, `hd*`, `xvd*`, `nvme*n*`, `mmcblk*`.
- 판별 기준: `/sys/block/<dev>/device/` 디렉토리 존재 여부 또는 `/sys/block/<dev>/loop/` 심링크 부재.

`mounts[]` 계약:
- **사용자 공간 파일시스템만** 포함한다. 커널·가상 마운트는 스토리지 평가 대상이 아니다.
- 수집 제외 fstype: `proc`, `sysfs`, `devtmpfs`, `devpts`, `hugetlbfs`, `debugfs`, `tracefs`, `squashfs`(snap), `overlay`(Docker), `cgroup`, `cgroup2`, `pstore`, `bpf`, `fusectl`, `efivarfs`, `configfs`, `securityfs`, `mqueue`, `ramfs`.
- 수집 포함: `ext4`, `xfs`, `btrfs`, `vfat`, `ntfs`, `tmpfs`(RAM-backed 실 용량 있음) 등.
- 판별 기준: `/proc/mounts`의 fstype 필드로 직접 분기.
- **`device` 필드 추가 필요**: `/proc/mounts`의 첫 번째 컬럼(마운트 소스 디바이스, 예: `/dev/sda2`)을 `device` 필드로 함께 전송한다.
  - 포털에서 파일시스템 ↔ 물리 디스크 연결(`sda2` → `sda`) 표시 가능
  - `tmpfs`, `proc` 등 실제 블록 디바이스가 없는 마운트를 `device` 필드로 판별 → 포털의 fstype 블록리스트(`_VIRTUAL_FSTYPES`) 의존도 감소
  - 현재 `InventoryMountInfo`에 `device` 필드 미포함으로 포털에서 구현 불가

**개선 방향 (P2)**
에이전트 업데이트(`device` 필드 추가 + 가상 마운트 필터링) 후:
- `web/services/filters.py`의 `_VIRTUAL_FSTYPES`, `_VIRTUAL_MOUNT_PREFIXES` 및 `is_virtual_mount()` 제거
- `mappers.py`의 `is_physical_disk()` 제거, `_PHYS_DISK_RE` 제거
- 포털에서 `device` 필드를 활용해 물리 디스크 ↔ 마운트 연결 UI 구현 가능

---

## 13. scheduler 서비스 미등록

**현재 설계**
`scheduler/` 디렉토리가 존재하고 `run_diagnostics()`가 `NotImplementedError`로 정의되어 있으나, `docker-compose.yml`에 서비스로 등록되어 있지 않다.

**개선 방향 (P3)**
구현 계획이 없으면 `scheduler/` 디렉토리 제거. 구현 예정이면 docker-compose에 서비스 추가 및 구현 완료.