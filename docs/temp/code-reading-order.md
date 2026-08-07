# 소스 읽기 순서

기준 시점: 2026-08-07, `develop` = `d9112f8` (이름·배치 정석화 직후).

전제는 저장소 바깥 껍데기(Dockerfile·compose·배포 스크립트·워크플로)와 `src/assessment_engine/` 의
개략 구조를 이미 봤다는 것이다. 여기서는 세부 모듈로 들어가는 순서만 정한다.

## 왜 이 순서인가

의존 방향을 실측하면 층이 깔끔하게 갈린다.

```
json_types  (계층 소속 없는 어휘)
    ^
domain      (right_sizing / service_classifier / boot_time — json_types 만 의존)
    ^
db          (config · domain · json_types)
    ^
consumer  web   (cache · config · contract · db · domain · json_types · log_config)
              ^
            worker  (web 재사용)
```

역방향 의존이 없다. 그래서 아래에서 위로 읽으면 모르는 이름을 만나지 않는다.

다만 순수 bottom-up 은 "이게 왜 필요한지" 모른 채 읽게 된다. 그래서 층을 훑는 단계(0·1·3)와
한 줄기를 끝까지 따라가는 수직 슬라이스(2·4·6)를 번갈아 둔다. 슬라이스는 계층의 역할이
한 흐름 안에서 드러나게 하는 장치다.

도메인(3단계)을 조회·표시보다 앞에 두는 이유는 그 뒤 계층이 전부 판정 결과를 나르기 때문이다.
`rollup_host` 가 무엇을 내는지 모르면 mapper 가 무슨 일을 하는지 보이지 않는다.

## 전체 분량

| 단계 | 대상 | 줄 수 | 성격 |
|------|------|-------|------|
| 0 | 어휘·배선 | 586 | 층 훑기 |
| 1 | 데이터 모양 | 1,335 | 층 훑기 |
| 2 | 수집 슬라이스 | 1,993 | 수직 |
| 3 | 도메인 판정 | 1,731 | 층 훑기 |
| 4 | 조회 슬라이스 | 893 | 수직 |
| 5 | 표시 슬라이스 | 1,604 | 수직 |
| 6 | 비동기 보고서 | 1,365 | 수직 |
| 7 | 화면별 확장 | 약 6,000 | 필요할 때 |

1~6 까지가 9,500줄 남짓이다. 7단계는 앞을 다 읽고 나면 같은 패턴의 반복이라 통독하지 않아도 된다.

---

## 0. 어휘·배선

| 파일 | 줄 | 무엇 |
|------|----|------|
| `json_types.py` | 40 | wire·JSONB 원본 JSON 값의 타입 별칭과 접근 헬퍼 |
| `contract.py` | 14 | 계약 버전 상수 둘 (에이전트 / 평가 API) |
| `config.py` | 271 | Settings 클래스 정의. 인스턴스는 여기서 만들지 않는다 |
| `log_config.py` | 64 | loguru 단일 설정 |
| `cache/redis.py` | 118 | `safe_*` helper — 모든 Redis 호출의 관문 |
| `db/session.py` | 50 | 세션 진입점 |
| `migrate.py` | 29 | alembic CLI 진입점 |

얻는 것은 전 계층이 공유하는 어휘와, 설정을 언제 만드는가(Composition Root)라는 규약이다.
`config.py` 에 인스턴스가 없는 이유를 이해하면 나머지 계층의 `get_*_settings()` 호출이 전부 읽힌다.

참고: CLAUDE.md #F4 · #F7 · #C3, `docs/reference/contracts/env.md`, `docs/reference/redis.md`

## 1. 데이터 모양

| 파일 | 줄 | 무엇 |
|------|----|------|
| `db/models/` 13파일 | 545 | ORM 테이블 정의 (시계열 7 + inventory + history + task + job + base) |
| `db/dtos/inbound.py` | 234 | 저장용 DTO (wire -> DB) |
| `db/dtos/outbound.py` | 556 | 조회 결과 DTO (repository -> service) |

모델을 먼저 보고 DTO 를 보면 왜 둘을 나눴는지가 보인다. inbound 는 Pydantic 검증을 통과한 값,
outbound 는 표시 파생이 하나도 없는 raw dataclass 다.

읽으면서 확인할 것 — 시계열 7테이블의 자연키 UNIQUE 가 무엇이고 왜 필요한가. 이게 2단계의
멱등성과 직결된다.

참고: `docs/reference/db/models.md` · `dtos.md`, CLAUDE.md #C1

## 2. 수집 슬라이스 (수직)

에이전트 메시지가 DB 행이 되기까지를 한 줄기로 따라간다.

| 순서 | 파일 | 줄 |
|------|------|----|
| 1 | `consumer/main.py` | 237 |
| 2 | `consumer/schemas.py` | 346 |
| 3 | `consumer/handlers/_common.py` | 290 |
| 4 | `consumer/handlers/metrics.py` | 80 |
| 5 | `consumer/mappers.py` | 481 |
| 6 | `db/repositories/collect.py` | 97 |
| 7 | `db/repositories/collect_sql.py` | 462 |

`main.py` 에서 토폴로지 선언과 핸들러 등록을 보고, `schemas.py` 로 무엇이 들어오는지 확인한 뒤,
`_common.py` 에서 공통 처리(멱등성·재시도·DLQ)를 잡고, `metrics.py` 로 한 routing key 를 끝까지 본다.
나머지 세 핸들러(inventory·task_result·error)는 같은 골격이라 훑기만 해도 된다.

얻는 것은 이 시스템의 실패 모델이다. 무엇이 DLQ 로 가고 무엇이 재시도되고 무엇이 조용히 ack 되는가.

읽으면서 확인할 것 — `safe_set_nx` 가 실패했을 때 왜 시스템이 안 깨지는가 (2단 방어).

참고: `docs/reference/consumer.md` · `rabbitmq.md`, CLAUDE.md #D1 · #D2

## 3. 도메인 판정

| 순서 | 파일 | 줄 |
|------|------|----|
| 1 | `domain/boot_time.py` | 39 |
| 2 | `domain/service_classifier.py` | 615 |
| 3 | `domain/right_sizing.py` | 1,077 |

작은 것부터 올라간다. `boot_time.py` 는 39줄인데 "왜 정확 비교를 안 하는가" 하나를 다루고,
`service_classifier.py` 는 카탈로그 하나에서 전부 파생시키는 패턴을 보여주고,
`right_sizing.py` 가 이 제품의 값어치다.

`right_sizing.py` 는 한 번에 읽지 말고 세 덩이로 나눈다.

| 덩이 | 범위 | 무엇 |
|------|------|------|
| 임계 상수 | 파일 상단 | 각 수치에 (계층, 출처)가 붙어 있다 |
| 자원별 판정 | `assess_cpu` ~ `assess_network` | 자원 하나를 USE 로 보는 법 |
| 호스트 종합 | `rollup_host` · `_host_status` · 처방 | 자원 5개를 인과로 묶는 법 |

읽으면서 확인할 것 — dual-gate 가 무엇을 막는가. 왜 이용률만으로도, 포화 신호만으로도 부족한가.

참고: `docs/reference/right-sizing.md` · `right-sizing-thresholds.md`, CLAUDE.md #E3

## 4. 조회 슬라이스 (수직)

URL 이 DTO 가 되기까지. 화면 중 가장 단순한 서버 목록을 고른다.

| 순서 | 파일 | 줄 |
|------|------|----|
| 1 | `web/main.py` · `web/deps.py` | 230 |
| 2 | `web/routers/pages/list_page.py` | 258 |
| 3 | `web/services/query/_base.py` | 100 |
| 4 | `web/services/query/server.py` | 224 |
| 5 | `db/repositories/query/server.py` | 47 |
| 6 | `db/repositories/query/server_sql.py` | 264 |

Protocol(`server.py`)을 구현(`server_sql.py`)보다 먼저 보는 것이 중요하다. 인터페이스가 계약이고
구현은 그 계약을 SQL 로 갚는 쪽이다.

얻는 것은 이 저장소가 의존성 역전을 실제로 어떻게 배선했는가다. `deps.py` 에서 구현이 주입되는
지점을 확인하면 #F4 의 "구현은 Composition Root 에서만" 이 코드로 보인다.

참고: `docs/reference/web/layering.md` · `routers.md` · `db/repositories.md`, CLAUDE.md #C2 · #F4

## 5. 표시 슬라이스 (수직)

DTO 가 화면이 되기까지. 4단계에서 받은 raw 를 이어서 따라간다.

| 순서 | 파일 | 줄 |
|------|------|----|
| 1 | `web/services/mappers/constants.py` | 87 |
| 2 | `web/services/mappers/resource_stats.py` | 82 |
| 3 | `web/services/mappers/assessment_display.py` | 206 |
| 4 | `web/services/mappers/server.py` | 941 |
| 5 | `web/view_models/server.py` | 288 |
| 6 | `web/templating/setup.py` · `filters.py` | 198 |

`resource_stats.py` 는 82줄인데 위치가 중요하다. 여기가 DB raw 를 도메인 입력으로 바꾸는 어댑터고,
`assessment_display.py` 가 도메인 판정을 화면 원자로 바꾼다. 이 둘이 3단계와 5단계를 잇는 다리다.

얻는 것은 P1~P4 원칙이 코드로 어떻게 나타나는가다. repository 가 왜 percent 를 계산하지 않는지,
템플릿이 왜 `length` 를 못 쓰는지가 여기서 납득된다.

읽으면서 확인할 것 — 같은 ViewModel 이 SSR·JSON·캐시 세 경로로 나가는데 왜 일관된가 (`enrich_*` idempotent).

참고: `docs/reference/web/services.md` · `view-models.md` · `static-assets.md`, CLAUDE.md #E1 P1~P4

## 6. 비동기 보고서 (수직)

프로세스가 갈리는 유일한 흐름이다. 발행 요청이 다른 프로세스에서 처리돼 돌아온다.

| 순서 | 파일 | 줄 |
|------|------|----|
| 1 | `web/routers/reports.py` | 241 |
| 2 | `web/services/diagnostic_service.py` | 240 |
| 3 | `worker/main.py` | 131 |
| 4 | `worker/lifecycle.py` | 39 |
| 5 | `worker/report_loop.py` | 94 |
| 6 | `web/services/report/generator.py` | 131 |
| 7 | `web/services/report/result.py` · `serializer.py` | 339 |
| 8 | `web/routers/_report_snapshot.py` | 94 |

라우터가 job 을 넣고 즉시 반환하는 지점 -> 워커가 claim 하는 지점 -> 스냅샷이 JSONB 로 굳는 지점 ->
다시 읽어 렌더하는 지점 순이다.

얻는 것은 상태를 DB 에 두면 프로세스 재시작이 왜 안전한가, graceful shutdown 이 무엇을 보장하는가다.
`worker/task_reaper.py`(56줄)도 같이 보면 "능동 정리 루프" 패턴이 하나 더 나온다.

읽으면서 확인할 것 — SIGTERM 이 왔을 때 진행 중인 job 하나가 어떻게 되는가.

참고: `docs/explanation/products/environment-report.md`, CLAUDE.md #C1 · #F11

## 7. 화면별 확장

여기부터는 통독 대상이 아니다. 앞 여섯 단계가 골격이고 나머지는 같은 골격 위의 변주라,
필요한 화면이 생겼을 때 그 줄기만 따라가면 된다.

| 묶음 | 파일 |
|------|------|
| 운영 신호·환경 개요 | `mappers/attention.py`(652) · `topology.py`(267) |
| 보고서 본문 | `mappers/report.py`(400) · `environment_report.py`(506) · `report_summary.py`(140) · `period_assessment.py`(424) |
| 메트릭·차트 | `mappers/metric_dashboard.py`(732) · `metric.py`(28) · `db/repositories/query/metric_sql.py`(1,649) |
| 계약 API | `routers/assessment.py`(91) · `mappers/assessment_api.py`(399) · `right_sizing_api.py`(260) |
| 원격 작업 | `services/task_service.py`(422) · `routers/tasks.py` · `consumer/task_policy.py`(90) |
| 부가 | `mappers/os_eol.py`(258) · `report_history.py`(90) · `api_reference.py`(130) · `service_reference.py`(28) |

`metric_sql.py` 1,649줄이 이 저장소 최대 파일인데, 차트 dispatch table 하나를 이해하면 나머지는
같은 형태의 반복이다. 처음부터 읽지 말고 `get_metric_trend` 의 dispatch 만 잡는 편이 낫다.

---

## 읽는 방식에 대한 메모

수직 슬라이스(2·4·5·6)는 파일을 오가며 읽게 된다. 한 파일을 끝까지 읽고 다음으로 가는 것보다,
호출을 따라 건너뛰면서 한 요청·한 메시지가 어디까지 가는지를 먼저 보는 편이 이해가 빠르다.
파일 통독은 그 흐름을 한 번 훑은 뒤에 해도 늦지 않다.

층 훑기(0·1·3)는 반대다. 이 파일들은 다른 곳에서 불려 다니는 어휘·판정이라 통독이 맞다.

각 단계 끝의 "읽으면서 확인할 것" 은 그 단계를 이해했는지 자가 점검하는 질문이다. 답이 안 나오면
해당 참고 문서를 보면 되고, 문서에도 없으면 그건 문서 쪽 결함이다.
