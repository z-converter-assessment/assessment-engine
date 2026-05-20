# Web 라우터

정책: CLAUDE.md #E2 · #F4. 본 문서는 라우터 모듈·endpoint 카탈로그·API versioning·breaking change 절차 단일 진실.

| 모듈 | 변수 | 접두사 | 응답 |
|------|------|--------|------|
| `routers/pages.py` | `pages_router` | `/servers` | HTML (Jinja2 SSR) |
| `routers/api.py` | `api_router` | `/api/v1/servers` | JSON (시계열·메트릭) + SSE |
| `routers/discovery.py` | `discovery_router` | `/api/v1/discovery` | JSON |
| `routers/tasks.py` | `tasks_router` | `/api/v1/tasks` | JSON |
| `routers/exports.py` | `exports_router` | `/api/v1/exports` | JSON (다운로드) |
| `routers/diagnostics.py` | `diagnostics_router` | `/api/v1/diagnostics` | JSON (ADR 0004) |
| `routers/diagnostic_results.py` | `diagnostic_results_router` | `/diagnostics` | HTML (SSR — 결과·이력 페이지) |

라우터 책임은 HTTP I/O만 — 비즈니스 로직은 service에 위임(#F4). JSON API는 `/api/v1/...` prefix 통일.

## SSR 페이지 (`pages.py`)

| 경로 | 핸들러 | 비고 |
|------|--------|------|
| `GET /servers/` | `list_servers` | 목록 + 검색·온라인 필터 + 4 액션 버튼 (발견/Install/Export/보고서). page=1 + 검색·필터 미사용 시 상단에 environment_overview + attention 두 섹션 노출 (`docs/architecture/web/services.md` "대시보드 상단 요약") |
| `GET /servers/report?ids=&period_days=&view=customer\|engineer` | `report` | 서버 보고서 (scope=server). USE Method 보고서. view=customer(양식 A — 고객 KPI) / view=engineer(양식 B — 16컬럼 정량). 동일 SQL·동일 템플릿, view 파라미터로 분기 (의의·근거는 `docs/products/server-report.md`) |
| `GET /servers/{server_id}` | `get_server` | detail 탭. 서버 진단 latest 카드 포함 (`to_panel_payload`) |
| `GET /servers/{server_id}/{cpu,memory,services,performance}` | 동일 helper | `_render_server_tab` 5 탭 공유 |
| `GET /servers/{server_id}/{storage,network}` | 별도 핸들러 | 다른 service 메서드 |

`_render_server_tab` helper — 5개 탭이 `service.get_server` + `{"server": ...}` context로 동일하게 렌더링되어 묶음. storage/network는 별도 service 메서드라 분리.

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

### `tasks.py` — 원격 작업 발행
| 경로 | 용도 |
|------|------|
| `POST /install` | ZConverter Install task 발행 (다중 서버 일괄). 부분 UNIQUE pending 중복 시 409 (`TaskDuplicatePending`) |

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
| `GET /diagnostics/results?ids=j1,j2,...` | 진단 결과 페이지 (polling으로 succeeded 추적) |
| `GET /diagnostics/history?days=&scope=&server_public_ids=` | 진단 발행 이력 (운영자 회고용) — `to_history_item` mapper 단일 진실 |

## 검증·에러 매핑

| HTTP | 의미 | 발생 위치 |
|------|------|-----------|
| 422 | 입력 형식 오류 | Pydantic field validator (IP 형식·UUID 형식·Literal enum) |
| 404 | 리소스 없음 | `resolve_internal_id` 또는 service `TaskNotFound`/`DiagnosticNotFound` exception |
| 409 | 충돌 | `tasks/install` pending 중복 (`TaskDuplicatePending`) 또는 진단 enqueue race (`DiagnosticRaceMiss`) |
| 500 | 서버 오류 | service 측 일반 Exception (probe 외부 네트워크 비정형 응답 등) |
| 503 | 설정 미충족 | `TaskNotConfigured` — ZDM_PACKAGE_SHA256 / SIZE_BYTES 미설정 시 install 발행 차단 |

## Breaking change 진화 절차

`/api/v1/...` endpoint에 backward-compatible 변경(응답 필드 추가·옵셔널 query param 추가·새 endpoint)은 `/v1/` 유지. backward-incompatible 변경(응답 필드 제거·의미 변경·required field 추가·HTTP 메서드 변경)이 필요한 경우 다음 절차:

1. `/api/v2/...` 신규 분기 + 기존 `/api/v1/...` 유지 — 라우터 모듈 분리 또는 동일 모듈 안 prefix 분기 어느 쪽이든. service 계층은 가능한 한 단일 진실, 라우터에서 v1 응답 변환 layer만 분기.
2. `/v1/` 응답 헤더 `Deprecation: true` + `Sunset: <ISO 8601 date>` 추가 (RFC 8594) — 6개월 후 제거 예정 표시.
3. 6개월 후 `/v1/` 라우터 제거.

본 프로젝트 현재 첫 v2 분기 사례 없음 — 도입 시 본 절 갱신.

엔진 측 self-host install bundle endpoint(`/zconverter.tar.gz`) 는 제거됨. task.install download.url 은 ZDM 측 contract (`http://{ZDM_IP}{ZDM_PACKAGE_PATH}`) 로 발행 — `docs/architecture/agent.md` "Download URL 조립 contract" 절 단일 진실.
