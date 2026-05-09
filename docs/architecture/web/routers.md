# Web 라우터

| 모듈 | 변수 | 접두사 | 응답 |
|------|------|--------|------|
| `routers/pages.py` | `pages_router` | `/servers` | HTML (Jinja2) |
| `routers/api.py` | `api_router` | `/api/v1/servers` | JSON |
| `routers/discovery.py` | `discovery_router` | `/api/v1/discovery` | JSON |
| `routers/tasks.py` | `tasks_router` | `/api/v1/tasks` | JSON |
| `routers/exports.py` | `exports_router` | `/api/v1/exports` | JSON (다운로드) |

라우터 책임은 HTTP I/O만 — 비즈니스 로직은 service에 위임 (F4).

## SSR 페이지 (`pages.py`)

| 경로 | 핸들러 | 비고 |
|------|--------|------|
| `GET /servers/` | `list_servers` | 목록 + 검색·온라인 필터 + 4 액션 버튼 (발견/Install/Export/보고서) |
| `GET /servers/report?ids=&period_days=` | `report` | USE Method 보고서 (양식 A·B 한 페이지) |
| `GET /servers/{server_id}` | `get_server` | detail 탭 |
| `GET /servers/{server_id}/{cpu,memory,services,performance}` | 동일 helper | `_render_server_tab` 5 탭 공유 |
| `GET /servers/{server_id}/{storage,network}` | 별도 핸들러 | 다른 service 메서드 |

`_render_server_tab` helper — 5개 탭이 `service.get_server` + `{"server": ...}` context로 동일하게 렌더링되어 묶음. storage/network는 별도 service 메서드라 분리.

## JSON API

### `api.py` — 시계열·메트릭·이벤트
| 경로 | 용도 |
|------|------|
| `GET /{id}/collection-status` | 마지막 metrics·inventory 시각 |
| `GET /{id}/metrics/latest` | 최신 dashboard (CPU/Mem/Disk/Net delta) |
| `GET /{id}/metrics/snapshots` | 시계열 페이지네이션 |
| `GET /{id}/metrics/chart?metric_type=&time_range=&bucket=&agg=` | 차트 시계열 (17 metric_type dispatcher) |
| `GET /{id}/events/reboot?time_range=&end=` | reboot/restart vertical marker용 |
| `GET /{id}/metrics/stream` | SSE — `metrics.events` 채널 |

### `discovery.py` — 도달성 검사
| 경로 | 용도 |
|------|------|
| `POST /probe` | IP HTTP HEAD probe (Ansible 배포 워크플로우 1단계) |

### `tasks.py` — 원격 작업 발행
| 경로 | 용도 |
|------|------|
| `POST /install` | ZConverter Install task 발행 (다중 서버 일괄). 409 = pending 중복 |

### `exports.py` — 정제 산출물
| 경로 | 용도 |
|------|------|
| `POST /inventory` | 정제 Inventory JSON (벤더 중립 v1 스키마). 클라이언트 다운로드 — 서버 stateless |

## 검증·에러 매핑

| HTTP | 의미 | 발생 위치 |
|------|------|-----------|
| 422 | 입력 형식 오류 | Pydantic field validator (IP 형식·UUID 형식·Literal enum) |
| 404 | 리소스 없음 | `resolve_internal_id` 또는 service `_NotFound` exception |
| 409 | 충돌 | `tasks/install` pending 중복 (`_DuplicatePending` exception) |
| 500 | 서버 오류 | service 측 일반 Exception (probe 외부 네트워크 비정형 응답 등) |
