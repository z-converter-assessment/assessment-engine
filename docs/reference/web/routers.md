# Web 라우터

정책: CLAUDE.md #E2 · #F4. 본 문서는 라우터 모듈·endpoint 카탈로그 단일 진실.

| 모듈 | 변수 | 접두사 | 응답 |
|------|------|--------|------|
| `routers/pages/__init__.py` | `pages_router` | (없음 — sub-router 자체 prefix) | HTML (Jinja2 SSR) |
| `routers/api.py` | `api_router` | `/api/servers` | JSON (시계열·메트릭) |
| `routers/tasks.py` | `tasks_router` | `/api/tasks` | JSON |
| `routers/assessment.py` | `assessment_router` | `/api/assessment` | JSON (재해복구/마이그레이션 소비 — 통합 프로비저닝 어세스먼트) |
| `routers/exports.py` | `exports_router` | `/api/exports` | JSON (다운로드 — assessment 계약 파일) |
| `routers/right_sizing.py` | `right_sizing_router` | `/api/right-sizing` | JSON (외부 자동화 소비 — 자원 적정성 판정) |
| `routers/reports.py` | `reports_router` | `/reports` | HTML (SSR — 환경 보고서·이력) + JSON (POST emit) |
| `routers/reports.py` | `reference_router` | (없음) | HTML (SSR — `/reference` 기준·임계값 참고) |

`pages_router` 는 추가 prefix 없이 sub-router 묶음만 — URL 명사 분리: 환경 개요(`/`)·서버 목록(`/servers`)·환경 단위(`/environment/*`)·서버 상세(`/servers/{id}/*`)·단일 보고서(`/servers/{id}/report`)·선택 N대 보고서(`/reports/servers`). 리터럴(`/servers`·`/environment/*`)이 `/servers/{id}` UUID 보다 먼저 매칭.

라우터 책임은 HTTP I/O만 — 비즈니스 로직은 service에 위임(#F4). JSON API는 `/api/...` prefix 통일.

## SSR 페이지 (`pages/`)

| 경로 | 핸들러 | 비고 |
|------|--------|------|
| `GET /` | `overview` | 환경 개요(홈) — 집계 위젯(환경 요약·주요 워크로드·자원 적정성·자원 이용·포화 7도넛·운영 이벤트/에러). 자동 갱신 없음(정적 집계). 카드 레이아웃은 `docs/explanation/products/dashboard.md` 단일 진실. environment_overview + attention (`docs/reference/web/services.md` "환경 개요 상단 요약") |
| `GET /servers?search=&is_online=&service=&os_distro=&classification=&os_eol=&fragment=` | `servers_list` | 서버 목록 — 검색·온라인·서비스·OS·프로비저닝·OS 지원상태 필터 + 선택 N대 액션 버튼 (Install/Export/보고서). 필터 AND 조합 — service category (web/db/cache/mq/container/monitor/remote/file/mail/infra) · os_distro (endoflife product slug 정확 일치) · classification (under/over/idle/optimal/insufficient_data) · os_eol (eol/supported/unknown — 판정 4단계를 필터에서는 셋으로 접는다). 검색 버튼 없음, 변경 즉시 client-side filter + URL replaceState. 기본 20행 표시 후 client clip(더보기/접기). `fragment=rows` 면 행 partial 만 |
| `GET /environment/assessment?time_range=&anchor_at=&fragment=` | `assessment` | 환경 자원 평가 — 윈도우/앵커 선택 -> 자원 적정성 분포 막대 + 서버별 자원 적정성 표(전 서버·전 분류, `build_action_targets` 단일 진실. 클라 20행 clip + 더보기). `fragment=result` 면 결과 partial 만(JS swap). time_range 기본 `DIAGNOSTIC_DEFAULT_TIME_RANGE`(14d) |
| `GET /environment/topology` | `topology` | 네트워크 토폴로지 — L3 subnet 공동소속 집계 그래프(subnet 노드 클릭 시 host 펼침) + 서브넷별 서버 카드 |
| `GET /environment/metrics?ids=` | `environment_metrics` | 환경(또는 선택 N대) 성능 추이 — 8차트 live. ids(public_ids) 면 선택 N대 한정, 제목 "선택 N대" |
| `GET /environment/realtime?ids=&fragment=` | `environment_realtime` | 환경(또는 선택 N대) 실시간 현황 — 이용률 2(CPU·메모리) + 신호 4 도넛 + 서버별 실시간 부하 sortable-table(7축 호스트당 1행 — 디스크 이용률은 표 전용, 도넛 없음. 칼럼 클릭 정렬). `realtime.js` 30초 polling(fragment swap). fragment=realtime 면 partial 만 |
| `GET /servers/{server_id}` | `get_server` | detail 탭 |
| `GET /servers/{server_id}/{cpu,memory,services,metrics}` | 동일 helper | `_render_server_tab` 탭 공유. metrics=성능 추이(자원별 `.perf-stack` 카드 + 카드 안 `.perf-grid`/`.perf-item` 낱개 차트, 화면·인쇄 모두 2열 — 인쇄는 A4 portrait 1페이지, `static-assets.md` 단일 진실) |
| `GET /servers/{server_id}/{storage,network}` | 별도 핸들러 | 다른 service 메서드 |
| `GET /servers/{server_id}/report?job=&view=&time_range=` | `single_server_report` | 단일 server 보고서. job 있으면 정적 스냅샷(ids 1개 발행이 이 URL 로 귀결), 없으면 read-only live preview. GET 은 record 안 함 |
| `GET /reports/servers?ids=&view=customer\|engineer&time_range=&job=` | `report` | 선택 N대 보고서 표시 (scope=server). job 있으면 정적 스냅샷, 없으면 read-only live preview (PRG). view 파라미터로 분기 |
| `POST /reports/servers/emit?ids=&view=&time_range=` | `report_emit` | 선택 N대 보고서 발행 record (PRG). ids 1개=단일 양식, 2개+=selection. `{view_url}` 응답 — JS navigate. 다시 보기/북마크/직접 URL 은 GET 만 → 중복 row 방지 |

`_render_server_tab` helper — cpu/memory/services/metrics 4개 탭이 `service.get_server` 결과 + 공통 context(period·resource_period·back chain·service_categories)로 동일하게 렌더링되어 묶음. detail(`/servers/{id}`)은 stability·recent_tasks·zdm_defaults 를 더 조회해 별도 핸들러, storage/network는 별도 service 메서드라 분리.

PRG (Post-Redirect-Get) 패턴 — 보고서 발행 시 record 와 표시 분리. `POST /reports/environment/emit` + `POST /reports/servers/emit` 가 record 책임, GET endpoint 는 read-only. 다시 보기 / 북마크 / 직접 URL 진입 시 record 안 됨 — 발행 시각만 다른 중복 row 방지.

## JSON API

### `api.py` — 시계열·메트릭·이벤트
| 경로 | 용도 |
|------|------|
| `GET /{id}/collection-status` | 마지막 metrics·inventory 시각 |
| `GET /{id}/metrics/latest` | 최신 dashboard (CPU/Mem/Disk/Net delta) |
| `GET /{id}/metrics/snapshots?cursor=&limit=` | 시계열 cursor pagination (#E2) |
| `GET /{id}/metrics/chart?metric_type=&time_range=&bucket=&agg=` | 차트 시계열 (metric_type dispatcher, 카탈로그는 `types.py`) |
| `GET /{id}/events/reboot?time_range=&end=` | reboot/restart vertical marker용 |
| `GET /environment/metrics-chart?metric_type=&time_range=&bucket=&ids=` (전체경로 `/api/servers/environment/metrics-chart` — api_router prefix) | 환경 시계열 차트 (환경 성능 추이 live + 대시보드 추이, ids 면 선택 N대) |
| `GET /api/fleet-status` (fleet_router) | 전역 데이터 최신성 — 온라인/전체 대수 + 마지막 수신 시각 (상단 바 폴링) |
| `GET /api/host-search?q=` (fleet_router) | 전역 호스트 검색 — hostname 부분일치 상위 8건 (상단 바 jump-to) |

### `tasks.py` — 원격 작업 발행 + 단건 조회
| 경로 | 용도 |
|------|------|
| `POST /install` | ZConverter Install task 발행 (다중 서버 일괄). 부분 UNIQUE pending 중복 시 409 (`TaskDuplicatePending`) |
| `GET /{task_id}` | 단일 task JSON — polling / list cell 갱신 callback 용 |
| `GET /{task_id}/detail` | 단일 task HTML fragment — task-modal body 용 (P3 정공, 서버 렌더 HTML 반환) |
| `GET /api/tasks?server_public_id=&limit=&cursor=` | 서버별 task 이력 — `server_public_id`(UUID) 필수, 시간 역순 cursor pagination(E2). `list_recent_tasks` -> `TaskSummaryItem[]` |

### `assessment.py` — 프로비저닝 어세스먼트 (재해복구/마이그레이션 소비)
| 경로 | 용도 |
|------|------|
| `GET /api/assessment?hostname=&ip=&public_id=&pair=&window_days=&end=` | 통합 프로비저닝 어세스먼트 JSON — 소스 서버를 관측해 타겟 VM 재현/수정 사이징에 필요한 것을 한 응답으로(identity/reproduction/sizing axes[]/assessment/diagnostics). 화면·보고서와 동일 산식(`report_aggregate` -> `rollup_host`, 재계산 0). 외부는 public_id 를 모르는 게 보통이라 hostname/ip 로 조회. 응답 구조·필드·불변식·버전 정본 = `docs/reference/contracts/assessment-api.md` |

### `exports.py` — assessment 계약 파일 전달
| 경로 | 용도 |
|------|------|
| `POST /inventory` | assessment 계약을 다운로드 JSON 파일로 (`GET /api/assessment` 와 데이터 동일, `docs/reference/contracts/assessment-api.md`). 필터 body, 클라이언트 다운로드 — 서버 stateless |

### `right_sizing.py` — 자원 적정성 판정 (외부 자동화 소비)
| 경로 | 용도 |
|------|------|
| `GET /api/right-sizing?hostname=&ip=&public_id=&pair=&window_days=&end=` | 서버별 자원 적정성 판정 JSON — 외부 자동화 소비. 화면·보고서와 동일 산식(`report_aggregate` -> `rollup_host`, 재계산 0). 외부는 내부 public_id 를 모르는 게 보통이라 hostname/ip 로 조회한다. 파라미터·응답 스키마·enum·권고 포맷·호스트명 충돌 안전은 Swagger(`/docs`)·ReDoc(`/redoc`)가 OpenAPI 스펙(라우터 docstring·Pydantic 응답 모델) 기준 단일 소유 |

`GET /reference/api` (`reference_router`) — 외부 연동 카탈로그만(OpenAPI 파생, 메서드·경로·요약·파라미터·요청 본문 필드명). 태그 화이트리스트(assessment/right-sizing/exports/tasks) + JSON 응답만 필터링 — 화면 전용 내부 데이터 조회(`api` 태그)·HTML fragment 엔드포인트는 제외(`services/mappers/api_reference.py` `_ALLOWED_TAGS`/`_returns_json`). 상세 스키마·enum·예시는 중복 문서화하지 않고 Swagger(`/docs`)·ReDoc(`/redoc`) 단일 진실로 위임(#F12·docs/README 1원칙).

### `reports.py` — 보고서 SSR + 발행 (PRG 패턴)
| 경로 | 용도 |
|------|------|
| `GET /reports/environment?job=&view=&time_range=&anchor_at=` | 환경 보고서 표시. job 있으면 정적 스냅샷 렌더, 없으면 발행 컨트롤만(본문 미생성). GET 은 read-only — record 안 함 (PRG) |
| `POST /reports/environment/emit?view=&time_range=&anchor_at=` | 환경 보고서 발행 record + `{view_url}` 응답 (JS navigate) |
| `GET /reports/history?days=&view=&scope=&server_public_ids=&limit=&fragment=&back=` | 보고서 발행 이력. 기본 20건, "더보기"가 `limit` 누적 재조회. `fragment=1` 시 partial HTML 만 (filter 변경 즉시 적용용) |
| `GET /reports/{job_id}/status` | 비동기 보고서 생성 상태 폴링 (pending/running/succeeded/failed) — `report-poll.js` |
| `GET /reference` | 참고 페이지 (`reference_router`) — 지표 정의(`_metric_definitions`) + 에이전트-엔진 데이터 계약·수집 함수 근거·assessment API 계약 요약(`_agent_contract_reference`) + 자원 적정성 평가 임계값·근거 계층·임계 상수 전체·Errors 축 설명(`_thresholds_reference`, recommendation 단일 진실) + 서비스 뱃지 카탈로그(`_service_badges`). 각 페이지 하단 `_reference_link.html` 은 제품명·버전 푸터만 렌더(보고서 꼬리 `_reference_footer.html` 도 이 partial 공유) — 참고 자료 진입은 사이드바 "참고" 그룹 단일 경로 |

## 검증·에러 매핑

| HTTP | 의미 | 발생 위치 |
|------|------|-----------|
| 422 | 입력 형식 오류 | Pydantic field validator (IP 형식·UUID 형식·Literal enum) |
| 404 | 리소스 없음 | `resolve_internal_id` 또는 service `TaskNotFound` exception |
| 409 | 충돌 | `tasks/install` pending 중복 (`TaskDuplicatePending`) |
| 500 | 서버 오류 | service 측 예기치 못한 Exception (DB·외부 의존 비정형 오류 등) |
| 503 | 발행 불가 | `TaskNotConfigured` — `HttpZdmPackageResolver` 메타 fetch 실패 (ZDM 도달 불가·HEAD non-200·size mismatch) 시 install 발행 차단 / `TaskPublishFailed` — broker 발행 실패 |

## URL 정책

URL prefix versioning (`/api/v1/...` / `/api/v2/...`) 안 함. 모든 JSON API 는 `/api/...` 직접 사용. 대부분 API 는 내부 front-end JS 전용이라 breaking change 시 라우터 + JS + docs 동시 정정(본 repo 안 일관). 예외는 `/api/right-sizing`·`/api/assessment` — 외부 자동화가 소비하는 계약이라 파괴적 변경 시 소비 측 고지가 필요하다.

task.install download.url 은 ZDM 측 contract (`http://{ZDM_IP}{ZDM_PACKAGE_PATH}`) 로 발행 — `docs/reference/contracts/agent-data.md` "Download URL 조립 contract" 절 단일 진실.
