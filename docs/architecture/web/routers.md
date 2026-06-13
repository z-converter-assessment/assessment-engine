# Web 라우터

정책: CLAUDE.md #E2 · #F4. 본 문서는 라우터 모듈·endpoint 카탈로그 단일 진실.

| 모듈 | 변수 | 접두사 | 응답 |
|------|------|--------|------|
| `routers/pages/__init__.py` | `pages_router` | (없음 — sub-router 자체 prefix) | HTML (Jinja2 SSR) |
| `routers/api.py` | `api_router` | `/api/servers` | JSON (시계열·메트릭) |
| `routers/tasks.py` | `tasks_router` | `/api/tasks` | JSON |
| `routers/exports.py` | `exports_router` | `/api/exports` | JSON (다운로드) |
| `routers/diagnostics.py` | `diagnostics_router` | `/api/diagnostics` | JSON (ADR 0004) |
| `routers/diagnostic_results.py` | `diagnostic_results_router` | `/diagnostics` | HTML (SSR — 결과·이력 페이지) |
| `routers/reports.py` | `reports_router` | `/reports` | HTML (SSR — 환경 보고서·이력) + JSON (POST emit) |
| `routers/reports.py` | `reference_router` | (없음) | HTML (SSR — `/reference` 기준·임계값 참고) |

`pages_router` 는 추가 prefix 없이 sub-router 묶음만 — URL 명사 분리: 환경 개요(`/`)·서버 목록(`/servers`)·환경 단위(`/environment/*`)·서버 상세(`/servers/{id}/*`)·단일 보고서(`/servers/{id}/report`)·선택 N대 보고서(`/reports/servers`). 리터럴(`/servers`·`/environment/*`)이 `/servers/{id}` UUID 보다 먼저 매칭.

라우터 책임은 HTTP I/O만 — 비즈니스 로직은 service에 위임(#F4). JSON API는 `/api/...` prefix 통일.

## SSR 페이지 (`pages/`)

| 경로 | 핸들러 | 비고 |
|------|--------|------|
| `GET /` | `overview` | 환경 개요(홈) — 집계 위젯(환경 요약·자원 적정성·운영 신호 + 환경 부하 추이·네트워크 토폴로지 요약). 자동 갱신 없음(정적 집계). environment_overview + attention (`docs/architecture/web/services.md` "대시보드 상단 요약") |
| `GET /servers?search=&is_online=&service=&os_id=&classification=&page=&limit=` | `servers_list` | 서버 목록 — 검색·온라인·서비스·OS·프로비저닝 필터 + 선택 N대 액션 버튼 (Install/Export/보고서). 필터 AND 조합 — service category (web/db/cache/mq/container/monitor) · os_id (distro 정확 일치) · classification (under/over/idle/shutdown/optimal/insufficient_data). 검색 버튼 없음, 변경 즉시 client-side filter + URL replaceState. 기본 20행 표시 후 client clip(더보기/접기). `fragment=rows` 면 행 partial 만 |
| `GET /environment/assessment?time_range=&anchor_at=&fragment=` | `assessment` | 환경 자원 평가 — 윈도우/앵커 선택 -> 자원 적정성 분류 + 리소스 부족 전체 목록(상위 N 절단 해제). `fragment=result` 면 결과 partial 만(JS swap). time_range 기본 `DASHBOARD_TIME_RANGE` |
| `GET /environment/topology` | `topology` | 네트워크 토폴로지 — L3 subnet 공동소속 집계 그래프(subnet 노드 클릭 시 host 펼침) + 서브넷별 서버 카드 |
| `GET /environment/metrics?ids=` | `environment_metrics` | 환경(또는 선택 N대) 성능 추이 — 10차트 live. ids(public_ids) 면 선택 N대 한정, 제목 "선택 N대" |
| `GET /environment/realtime?ids=&fragment=` | `environment_realtime` | 환경(또는 선택 N대) 실시간 현황 — 평균 활용률 도넛 + 네트워크·디스크 I/O + 부하 상위 탑5. 정적 렌더(자동 갱신 없음). fragment=realtime 면 partial 만 |
| `GET /servers/{server_id}` | `get_server` | detail 탭. 서버 진단 latest 카드 포함 |
| `GET /servers/{server_id}/{cpu,memory,services,metrics}` | 동일 helper | `_render_server_tab` 탭 공유. metrics=성능 추이(추이 차트 5행2열 `.perf-merged` 단일 카드) |
| `GET /servers/{server_id}/{storage,network}` | 별도 핸들러 | 다른 service 메서드 |
| `GET /servers/{server_id}/report?view=&time_range=` | `single_server_report` | 단일 server 보고서 read-only. record 안 함 (1대 단위는 발행 흐름 없음) |
| `GET /reports/servers?ids=&view=customer\|engineer&time_range=&job=` | `report` | 선택 N대 보고서 표시 (scope=server). job 있으면 정적 스냅샷, 없으면 read-only live preview (PRG). view 파라미터로 분기 |
| `POST /reports/servers/emit?ids=&view=&time_range=` | `report_emit` | 선택 N대 보고서 발행 record (PRG). ids 1개=단일 양식, 2개+=selection. `{view_url}` 응답 — JS navigate. 다시 보기/북마크/직접 URL 은 GET 만 → 중복 row 방지 |

`_render_server_tab` helper — 5개 탭이 `service.get_server` + `{"server": ...}` context로 동일하게 렌더링되어 묶음. storage/network는 별도 service 메서드라 분리.

PRG (Post-Redirect-Get) 패턴 — 보고서 발행 시 record 와 표시 분리. `POST /reports/environment/emit` + `POST /reports/servers/emit` 가 record 책임, GET endpoint 는 read-only. 다시 보기 / 북마크 / 직접 URL 진입 시 record 안 됨 — 발행 시각만 다른 중복 row 방지.

## JSON API

### `api.py` — 시계열·메트릭·이벤트
| 경로 | 용도 |
|------|------|
| `GET /{id}/collection-status` | 마지막 metrics·inventory 시각 |
| `GET /{id}/metrics/latest` | 최신 dashboard (CPU/Mem/Disk/Net delta) |
| `GET /{id}/metrics/snapshots?cursor=&limit=` | 시계열 cursor pagination (#E2) |
| `GET /{id}/metrics/chart?metric_type=&time_range=&bucket=&agg=` | 차트 시계열 (17 metric_type dispatcher) |
| `GET /{id}/events/reboot?time_range=&end=` | reboot/restart vertical marker용 |

### `tasks.py` — 원격 작업 발행 + 단건 조회
| 경로 | 용도 |
|------|------|
| `POST /install` | ZConverter Install task 발행 (다중 서버 일괄). 부분 UNIQUE pending 중복 시 409 (`TaskDuplicatePending`) |
| `GET /{task_id}` | 단일 task JSON — polling / list cell 갱신 callback 용 |
| `GET /{task_id}/detail` | 단일 task HTML fragment — task-modal body 용 (P3 정공, JS HTML 합성 폐기) |

### `exports.py` — 정제 산출물
| 경로 | 용도 |
|------|------|
| `POST /inventory` | 정제 Inventory JSON (`docs/architecture/web/export-schema.md`). envelope에 period_window + size_class_guide 포함. 클라이언트 다운로드 — 서버 stateless |

### `diagnostics.py` — 진단 (ADR 0004 + 0010)
| 경로 | 용도 |
|------|------|
| `POST /` | scope=server|environment 진단 enqueue. server_ids batch. active partial UNIQUE 충돌 시 기존 job_id 반환. 400/404/409는 `DiagnosticBadRequest`/`DiagnosticNotFound`/`DiagnosticRaceMiss` 매핑 |
| `GET /?ids=j1,j2,...` | N개 batch polling. UUID 형식 검증 (422), 100건 상한 |
| `GET /{job_id}` | 단건 polling 편의 |

### `diagnostic_results.py` — SSR 결과·이력 (ADR 0004)
| 경로 | 용도 |
|------|------|
| `GET /diagnostics?ids=j1,j2,...` | 진단 결과 페이지 (polling 으로 succeeded 추적). environment scope job 마다 `/reports/environment` iframe 2 view 미리 합성 |
| `GET /diagnostics/history?days=&scope=&server_public_ids=&full=` | 진단 발행 이력 — job_type='ai_diagnostic' 자동 필터, scope 으로 environment/server 분기. 기본 20건 + `full=1` 시 전체 (보고서 이력과 동일 패턴) |

### `reports.py` — 보고서 SSR + 발행 (PRG 패턴)
| 경로 | 용도 |
|------|------|
| `GET /reports/environment?view=&time_range=&anchor_at=` | 환경 보고서 표시. GET 은 read-only — record 안 함 (PRG) |
| `POST /reports/environment/emit?view=&time_range=&anchor_at=` | 환경 보고서 발행 record + `{view_url}` 응답 (JS navigate) |
| `GET /reports/history?days=&view=&scope=&server_public_ids=&full=&fragment=` | 보고서 발행 이력. 기본 20건 + `full=1` 시 전체. `fragment=1` 시 partial HTML 만 (filter 변경 즉시 적용용) |
| `GET /reference` | 참고 페이지 (`reference_router`) — 지표 정의(`_metric_definitions`: 활용률·집계·환경/대시보드 지표 계산 정의) + 자원 적정성 분류 임계값(`_thresholds_reference`, recommendation 단일 진실). 각 페이지 하단 `_reference_link` 가 `#metric-definitions` 앵커로 링크. 사이드바 "참고" 그룹 |

## 검증·에러 매핑

| HTTP | 의미 | 발생 위치 |
|------|------|-----------|
| 422 | 입력 형식 오류 | Pydantic field validator (IP 형식·UUID 형식·Literal enum) |
| 404 | 리소스 없음 | `resolve_internal_id` 또는 service `TaskNotFound`/`DiagnosticNotFound` exception |
| 409 | 충돌 | `tasks/install` pending 중복 (`TaskDuplicatePending`) 또는 진단 enqueue race (`DiagnosticRaceMiss`) |
| 500 | 서버 오류 | service 측 예기치 못한 Exception (DB·외부 의존 비정형 오류 등) |
| 503 | 설정 미충족 | `TaskNotConfigured` — `HttpZdmPackageResolver` 메타 fetch 실패 (ZDM 도달 불가·HEAD non-200·size mismatch) 시 install 발행 차단 |

## URL 정책 (ADR 0021)

URL prefix versioning (`/api/v1/...` / `/api/v2/...`) 안 함. 모든 JSON API 는 `/api/...` 직접 사용. B2B 내부 포털이라 외부 client 없음 — breaking change 시 라우터 + front-end JS + docs 동시 정정 (본 repo 안 일관). 외부 contract 도입 시 별도 결정.

엔진 측 self-host install bundle endpoint(`/zconverter.tar.gz`) 는 제거됨 (ADR 0016). task.install download.url 은 ZDM 측 contract (`http://{ZDM_IP}{ZDM_PACKAGE_PATH}`) 로 발행 — `docs/architecture/agent.md` "Download URL 조립 contract" 절 단일 진실.

## dev 한정 라우터 (ADR 0018)

| 모듈 | 변수 | 경로 | 등록 조건 |
|------|------|------|-----------|
| `routers/dev_zdm_mock.py` | `dev_zdm_mock_router` | `GET {ZDM_PACKAGE_PATH}` (default `/download/ZConverter_CloudSource_Setup_Linux.tar.gz`) | `app_env == "dev"` 일 때만 `web/main.py` 가 include_router. prod 등록 안 됨 |

용도: install task E2E (publish → agent worker download → install.sh exec → task.result → consumer UPDATE → list UI badge) 를 dev 한정으로 실증. 더미 tar.gz (startup 1회 in-memory build) + ETag/Content-Length/Last-Modified 응답 헤더로 `HttpZdmPackageResolver` HEAD/GET 흐름 정합. install.sh 는 인자 echo + exit 0 만 — 실제 ZConverter 설치 동작은 평가 범위 밖.
