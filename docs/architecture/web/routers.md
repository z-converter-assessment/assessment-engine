# Web 라우터

정책: CLAUDE.md #E2 · #F4. 본 문서는 라우터 모듈·endpoint 카탈로그 단일 진실.

| 모듈 | 변수 | 접두사 | 응답 |
|------|------|--------|------|
| `routers/pages.py` | `pages_router` | `/servers` | HTML (Jinja2 SSR) |
| `routers/api.py` | `api_router` | `/api/servers` | JSON (시계열·메트릭) + SSE |
| `routers/discovery.py` | `discovery_router` | `/api/discovery` | JSON |
| `routers/tasks.py` | `tasks_router` | `/api/tasks` | JSON |
| `routers/exports.py` | `exports_router` | `/api/exports` | JSON (다운로드) |
| `routers/diagnostics.py` | `diagnostics_router` | `/api/diagnostics` | JSON (ADR 0004) |
| `routers/diagnostic_results.py` | `diagnostic_results_router` | `/diagnostics` | HTML (SSR — 결과·이력 페이지) |
| `routers/reports.py` | `reports_router` | `/reports` | HTML (SSR — 환경 보고서·이력·참고자료) + JSON (POST emit) |
| `routers/pages/report_page.py` | `report_page_router` | `/servers` | HTML (SSR — server scope 보고서) + JSON (POST emit) |

라우터 책임은 HTTP I/O만 — 비즈니스 로직은 service에 위임(#F4). JSON API는 `/api/...` prefix 통일.

## SSR 페이지 (`pages.py`)

| 경로 | 핸들러 | 비고 |
|------|--------|------|
| `GET /servers/?search=&is_online=&service=&os_id=&classification=` | `list_servers` | 목록 + 검색·온라인·서비스·OS·프로비저닝 필터 + 4 액션 버튼 (발견/Install/Export/보고서). page=1 일 때 상단에 environment_overview + attention 두 섹션 노출 (`docs/architecture/web/services.md` "대시보드 상단 요약"). 필터는 모두 AND 조합 — service category (web/db/cache/mq/container/monitor) · os_id (distro 정확 일치) · classification (under/over/idle/shutdown/optimal/insufficient_data). 검색 버튼 없음, dropdown/checkbox 즉시 client-side filter + URL replaceState |
| `GET /servers/report?ids=&view=customer\|engineer&time_range=` | `report` | 서버 보고서 표시 (scope=server). GET 은 read-only — record 안 함 (PRG). 동일 SQL·동일 템플릿, view 파라미터로 분기 |
| `POST /servers/report/emit?ids=&view=&time_range=` | `report_emit` | 서버 보고서 발행 record (PRG). `record_report_emission` 호출 + `{view_url}` 응답 — JS 가 navigate. 다시 보기/북마크/직접 URL 은 GET 만 → 중복 row 방지 |
| `GET /servers/{server_id}` | `get_server` | detail 탭. 서버 진단 latest 카드 포함 (DIAGNOSTIC_ENABLED=false 시 skip) |
| `GET /servers/{server_id}/{cpu,memory,services,performance}` | 동일 helper | `_render_server_tab` 5 탭 공유 |
| `GET /servers/{server_id}/{storage,network}` | 별도 핸들러 | 다른 service 메서드 |
| `GET /servers/{server_id}/report?view=&time_range=` | `single_server_report` | 단일 server 보고서 read-only. record 안 함 (1대 단위는 발행 흐름 없음) |

`_render_server_tab` helper — 5개 탭이 `service.get_server` + `{"server": ...}` context로 동일하게 렌더링되어 묶음. storage/network는 별도 service 메서드라 분리.

PRG (Post-Redirect-Get) 패턴 — 보고서 발행 시 record 와 표시 분리. `POST /reports/environment/emit` + `POST /servers/report/emit` 가 record 책임, GET endpoint 는 read-only. 다시 보기 / 북마크 / 직접 URL 진입 시 record 안 됨 — 발행 시각만 다른 중복 row 방지.

## JSON API

### `api.py` — 시계열·메트릭·이벤트
| 경로 | 용도 |
|------|------|
| `GET /{id}/collection-status` | 마지막 metrics·inventory 시각 |
| `GET /{id}/metrics/latest` | 최신 dashboard (CPU/Mem/Disk/Net delta) |
| `GET /{id}/metrics/snapshots?cursor=&limit=` | 시계열 cursor pagination (#E2) |
| `GET /{id}/metrics/chart?metric_type=&time_range=&bucket=&agg=` | 차트 시계열 (17 metric_type dispatcher) |
| `GET /{id}/events/reboot?time_range=&end=` | reboot/restart vertical marker용 |
| `GET /{id}/metrics/stream` | SSE — `text/event-stream` (Consumer PUB -> Redis -> SSE) |

### `discovery.py` — 도달성 검사
| 경로 | 용도 |
|------|------|
| `POST /probe` | IP HTTP probe (에이전트 배포 워크플로우 1단계). 응답 `{reachable, status_code, latency_ms}` |

설계 결정:
- 타임아웃 5초 — 폐쇄망 LAN 가정. HTTP 80/443은 보통 열려있어 가벼운 도달성 검사로 적합
- `ipaddress.ip_address()` 파싱 검증 — IPv4/IPv6 형식 422 차단
- SSRF 방지(localhost·메타데이터 IP 차단) 미적용 — 폐쇄망 가정상 운영자 의도 입력으로 간주
- ICMP 미사용 — raw socket 권한 필요라 회피
- fail-open (#F6) — HTTP 도달은 SSH 도달을 의미하지 않음. 1차 필터일 뿐. 추후 SSH credential 등록 + ansible 실행 흐름이 본 단계 위에 얹힘

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
| `GET /reports/right-sizing-thresholds` | Right-sizing 분류 임계값 참고자료 (recommendation 모듈 단일 진실 시각화) |

## 검증·에러 매핑

| HTTP | 의미 | 발생 위치 |
|------|------|-----------|
| 422 | 입력 형식 오류 | Pydantic field validator (IP 형식·UUID 형식·Literal enum) |
| 404 | 리소스 없음 | `resolve_internal_id` 또는 service `TaskNotFound`/`DiagnosticNotFound` exception |
| 409 | 충돌 | `tasks/install` pending 중복 (`TaskDuplicatePending`) 또는 진단 enqueue race (`DiagnosticRaceMiss`) |
| 500 | 서버 오류 | service 측 일반 Exception (probe 외부 네트워크 비정형 응답 등) |
| 503 | 설정 미충족 | `TaskNotConfigured` — `HttpZdmPackageResolver` 메타 fetch 실패 (ZDM 도달 불가·HEAD non-200·size mismatch) 시 install 발행 차단 |

## URL 정책 (ADR 0021)

URL prefix versioning (`/api/v1/...` / `/api/v2/...`) 안 함. 모든 JSON API 는 `/api/...` 직접 사용. B2B 내부 포털이라 외부 client 없음 — breaking change 시 라우터 + front-end JS + docs 동시 정정 (본 repo 안 일관). 외부 contract 도입 시 별도 결정.

엔진 측 self-host install bundle endpoint(`/zconverter.tar.gz`) 는 제거됨 (ADR 0016). task.install download.url 은 ZDM 측 contract (`http://{ZDM_IP}{ZDM_PACKAGE_PATH}`) 로 발행 — `docs/architecture/agent.md` "Download URL 조립 contract" 절 단일 진실.

## dev 한정 라우터 (ADR 0018)

| 모듈 | 변수 | 경로 | 등록 조건 |
|------|------|------|-----------|
| `routers/dev_zdm_mock.py` | `dev_zdm_mock_router` | `GET {ZDM_PACKAGE_PATH}` (default `/download/ZConverter_CloudSource_Setup_Linux.tar.gz`) | `app_env == "dev"` 일 때만 `web/main.py` 가 include_router. prod 등록 안 됨 |

용도: install task E2E (publish → agent worker download → install.sh exec → task.result → consumer UPDATE → list UI badge) 를 dev 한정으로 실증. 더미 tar.gz (startup 1회 in-memory build) + ETag/Content-Length/Last-Modified 응답 헤더로 `HttpZdmPackageResolver` HEAD/GET 흐름 정합. install.sh 는 인자 echo + exit 0 만 — 실제 ZConverter 설치 동작은 평가 범위 밖.
