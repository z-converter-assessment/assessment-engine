# 개선 권고사항

현재 동작에는 문제가 없지만, 코드 품질·확장성·정확성 측면에서 개선하면 좋을 항목들을 기록한다.
우선순위 순으로 정렬했다.

---

## P1 — 버그 수준 (즉시 개선 권장)

### 1. Alembic 마이그레이션 도입

**현황**
web lifespan이 `CREATE EXTENSION + create_all + create_hypertable`을 수행한다. 컬럼 추가·변경·삭제 등 스키마 변경 시 `create_all`은 기존 테이블을 건드리지 않으므로 변경이 적용되지 않는다.

**문제**
스키마 변경이 필요할 때 수동으로 `docker compose down -v` 후 재기동하거나, DB에 직접 DDL을 실행해야 한다. 프로덕션에서는 데이터 유실 없이 마이그레이션을 수행할 수단이 없다.

**개선 방향**
- Alembic 초기화 및 `env.py` 설정
- 현재 스키마를 초기 마이그레이션으로 작성 (`create_hypertable` 포함)
- `consumer depends_on web: service_healthy` 제거 → consumer가 DB에 직접 의존

---

## P2 — 설계 개선 (기능 확장 전 권장)

### 2. Redis 캐시 TTL을 config로 통합

**현황**
`query_service.py`에 캐시 TTL이 하드코딩되어 있다.
```python
await self.redis.set(cache_key, ..., ex=300)   # 인벤토리 캐시
await self.redis.set(cache_key, ..., ex=60)    # 메트릭 캐시
```

`config.py`에는 `redis_ttl_idempotent`, `redis_ttl_online`, `redis_ttl_token`만 정의되어 있고 캐시 TTL은 없다.

**문제**
캐시 TTL 변경 시 코드를 직접 수정해야 하며, `.env`로 런타임 조정이 불가능하다. TTL 값이 문서(`CLAUDE.md`)와 코드 두 곳에 분산되어 동기화 오류 가능성이 있다.

**개선 방향**
```python
# config.py
redis_ttl_cache_inventory: int = 300
redis_ttl_cache_metrics: int = 60
```

---

### 3. counter reset 감지: boot_time 기반 건너뛰기

**현황**
두 시점의 delta가 음수이면 `None` 처리 후 UI에서 "—" 표시. 재부팅·에이전트 재시작 시 카운터가 0으로 리셋되어 발생하는 delta < 0을 음수 delta와 동일하게 처리한다.

**문제**
재부팅 후 첫 샘플은 정상 delta임에도 "—"로 표시된다. `boot_time`을 비교하면 카운터 리셋 여부를 감지해 해당 샘플만 정확하게 건너뛸 수 있다.

**개선 방향**
`server_metrics`에 `boot_time` 컬럼을 추가하거나, 두 시점의 `boot_time`이 다르면 delta 계산을 건너뛰는 로직을 `MetricsCalculator`에 추가.

---

### 4. SSE 채널 분리: 서버별 구독

**현황**
모든 SSE 연결이 단일 `metrics.events` 채널을 구독하고, 서버 측에서 `server_id` 일치 여부로 필터링한다.

**문제**
서버 수와 동시 접속자 수가 늘어나면, 각 SSE 연결이 자신과 무관한 이벤트를 수신·버리는 비율이 높아진다.

**개선 방향**
채널을 `metrics.events.{server_id}`로 분리. 각 SSE 연결이 해당 서버의 채널만 구독. consumer는 `redis.publish(f"metrics.events.{server_id}", ...)`로 발행.

---

## P3 — 코드 정리 (리팩토링)

### 5. InventoryMountInfo 필드 명확화

**현황**
`consumer/schemas.py`의 `InventoryMountInfo`에 `free_bytes`, `avail_bytes` 필드가 정의되어 있으나, `handler.py`에서 인벤토리 저장 시 이 필드들은 무시된다. 동적 사용량은 `server_mount_usage`에 별도 저장한다.

**문제**
에이전트 메시지를 보면 `InventoryMountInfo`에 `free_bytes`가 있어 저장될 것으로 오해할 수 있다. 코드를 처음 보는 사람이 혼란을 겪을 수 있다.

**개선 방향**
`handler.py`의 `_to_inventory_create()` 내부에 주석으로 의도를 명시하거나, `InventoryMountInfo`에 필드 수준 docstring 추가.

---

### 6. at-least-once 멱등성 전환 검토

**현황**
`SET NX` → DB 커밋 순서. 크래시 시 데이터 유실(at-most-once).

**현재 수용 이유**
메트릭 중복 삽입이 delta 오염을 유발하므로 유실이 낫다 (TRADEOFFS.md #1 참조).

**재검토 조건**
향후 `(server_id, collected_at)` 복합 UNIQUE 제약을 추가하면 DB 레벨에서 중복 삽입을 막을 수 있다. 이 경우 순서를 DB 커밋 → SET NX로 바꿔 at-least-once로 전환 가능하다.

---

### 7. scheduler 서비스 미등록 해소

**현황**
`scheduler/` 디렉토리가 존재하고 `run_diagnostics()`가 `NotImplementedError`로 정의되어 있으나, `docker-compose.yml`에 서비스로 등록되어 있지 않다.

**개선 방향**
구현 계획이 없으면 `scheduler/` 디렉토리 제거. 구현 예정이면 docker-compose에 서비스 추가 및 구현 완료.