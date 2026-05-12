# Web 라우터

| 모듈 | 변수 | 접두사 | 응답 |
|------|------|--------|------|
| `routers/pages.py` | `pages_router` | `/servers` | HTML (Jinja2 SSR) |
| `routers/api.py` | `api_router` | `/api/v1/servers` | JSON (시계열·메트릭) + SSE |
| `routers/discovery.py` | `discovery_router` | `/api/v1/discovery` | JSON |
| `routers/tasks.py` | `tasks_router` | `/api/v1/tasks` | JSON |
| `routers/exports.py` | `exports_router` | `/api/v1/exports` | JSON (다운로드) |
| `routers/diagnostics.py` | `diagnostics_router` | `/api/v1/diagnostics` | JSON (ADR 0004) |
| `routers/diagnostic_results.py` | `diagnostic_results_router` | `/diagnostics` | HTML (SSR — 결과·이력 페이지) |
| `routers/payloads.py` | `payloads_router` | (root) | application/gzip — agent install bundle |

라우터 책임은 HTTP I/O만 — 비즈니스 로직은 service에 위임 (F4). API versioning은 #F13 (`/api/v1/`).

## SSR 페이지 (`pages.py`)

| 경로 | 핸들러 | 비고 |
|------|--------|------|
| `GET /servers/` | `list_servers` | 목록 + 검색·온라인 필터 + 4 액션 버튼 (발견/Install/Export/보고서). page=1 + 검색·필터 미사용 시 상단에 environment_overview + attention 두 섹션 노출 (`docs/architecture/web/services.md` "대시보드 상단 요약") |
| `GET /servers/report?ids=&period_days=&view=customer\|engineer` | `report` | USE Method 보고서. view=customer(양식 A — 고객 KPI) / view=engineer(양식 B — 15컬럼 정량). 동일 SQL·동일 템플릿, view 파라미터로 분기 (`docs/architecture/deliverables.md`) |
| `GET /servers/{server_id}` | `get_server` | detail 탭. AI 진단 latest 카드 포함 (`to_panel_payload`) |
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
| `POST /probe` | IP HTTP probe (Ansible 배포 워크플로우 1단계). httpx 5초 timeout, ipaddress 형식 검증, fail-open (#F6) |

### `tasks.py` — 원격 작업 발행
| 경로 | 용도 |
|------|------|
| `POST /install` | ZConverter Install task 발행 (다중 서버 일괄). 부분 UNIQUE pending 중복 시 409 (`_DuplicatePending`) |

### `exports.py` — 정제 산출물
| 경로 | 용도 |
|------|------|
| `POST /inventory` | 정제 Inventory JSON v3 (`docs/architecture/inventory-export.md`). envelope에 period_window + size_class_guide 포함. 클라이언트 다운로드 — 서버 stateless |

### `diagnostics.py` — AI 진단 (ADR 0004)
| 경로 | 용도 |
|------|------|
| `POST /` | scope=server|environment 진단 enqueue. server_ids batch. active partial UNIQUE 충돌 시 기존 job_id 반환 |
| `GET /?ids=j1,j2,...` | N개 batch polling. UUID 형식 검증 (422), 100건 상한 |
| `GET /{job_id}` | 단건 polling 편의 |

### `diagnostic_results.py` — SSR 결과·이력 (ADR 0004)
| 경로 | 용도 |
|------|------|
| `GET /diagnostics/results?ids=j1,j2,...` | 진단 결과 페이지 (polling으로 succeeded 추적) |
| `GET /diagnostics/history?scope=&limit=&offset=` | 진단 발행 이력 (운영자 회고용) |

### `payloads.py` — agent install bundle
| 경로 | 용도 |
|------|------|
| `GET /zconverter.tar.gz` | agent의 hardcoded fetch path. in-memory tar.gz 생성, `install.sh` mode=0o755 메타 박힘. `/api/v1/` prefix 없음 — agent 계약 path 우선 (#F13 예외) |

## 검증·에러 매핑

| HTTP | 의미 | 발생 위치 |
|------|------|-----------|
| 422 | 입력 형식 오류 | Pydantic field validator (IP 형식·UUID 형식·Literal enum) |
| 404 | 리소스 없음 | `resolve_internal_id` 또는 service `_NotFound` exception |
| 409 | 충돌 | `tasks/install` pending 중복 (`_DuplicatePending` exception) |
| 500 | 서버 오류 | service 측 일반 Exception (probe 외부 네트워크 비정형 응답 등) |
