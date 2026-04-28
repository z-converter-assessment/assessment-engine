# 향후 아키텍처 확장 방향

## 배경

현재 구조는 에이전트 → MQ → consumer → DB → web 흐름으로 인벤토리·메트릭을 수집·조회한다.
이후 수집된 데이터를 기반으로 **LLM을 활용한 자동 진단** 기능을 추가하는 방향을 논의했다.

---

## 신규 컴포넌트: scheduler

### 역할
주기적으로 서버 상태를 진단하는 독립 asyncio 프로세스.
consumer와 동일하게 FastAPI DI 없이 `get_db()`, `get_redis()`를 직접 호출한다.

### 구현 방식
단순 주기 실행이면 순수 asyncio 루프로 충분하다.
크론 표현식·특정 시각 실행 등 복잡한 스케줄이 필요하면 APScheduler(AsyncIOScheduler) 사용을 검토한다.

```
web      — 쿼리/렌더링
consumer — MQ 소비 및 DB 저장
scheduler — 진단 스케줄링  ← 신규
```

---

## 진단 로직 흐름

```
스케줄러 기동
  → DB에서 서버 인벤토리 + 최신 메트릭 조회
  → Redis에서 온라인 상태 확인 (online:{server_id})
  → RAG: 벡터DB에서 유사 진단 사례 검색
  → 프롬프트 구성 (인벤토리 + 메트릭 + RAG 결과)
  → LLM API 호출 (httpx 비동기 요청)
  → 진단 결과 DB 저장
  → Redis PUB/SUB으로 web에 알림 (선택)
```

LLM 호출은 `httpx.AsyncClient`로 비동기 HTTP 요청이라 asyncio와 자연스럽게 통합된다.

---

## 현재 구조 대비 추가 필요 항목

### 벡터DB (RAG용)
- **pgvector**: PostgreSQL 확장으로 추가. 현재 DB에 `CREATE EXTENSION vector`만 추가하면 된다.
- **별도 서비스**: Chroma, Qdrant 등. 규모가 커지면 검토.
- 초기에는 pgvector로 시작하는 것이 현재 구조와 가장 자연스럽다.

### ORM 모델 추가
- `DiagnosticReport` — LLM 진단 결과 저장 테이블
  - server_id (FK)
  - 진단 결과 텍스트
  - 사용된 메트릭 스냅샷 참조
  - 생성 시각

### Redis 키 추가 (선택)
| 용도 | 키 | TTL |
|------|----|-----|
| 진단 결과 알림 | `diag.events` PUB/SUB 채널 | — |

---

## 현재 CLAUDE.md 반영 여부

scheduler/LLM 관련 내용은 미구현 상태이므로 CLAUDE.md에는 반영하지 않는다.
구현이 시작되는 시점에 CLAUDE.md에 추가한다.