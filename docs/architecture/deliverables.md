# Assessment 산출물 — Workflow 통합

본 엔진의 운영자 워크플로우 4 산출물 흐름. 각 산출물은 web 라우터·서비스·산출 결과 형식이 분리돼 있어, 본 문서가 통합 진입점.

| 산출물 | 라우터 | 서비스 | 산출 형식 | 단일 진실 |
|--------|--------|--------|-----------|-----------|
| 서버 발견 | `discovery.py` | (router inline — httpx probe) | JSON `{reachable, status_code, latency_ms}` | 본 문서 "서버 발견" |
| ZConverter Install task | `tasks.py` | `task_service.py` | DB `tasks` 이력 + Redis pending + agent RPC reply | `docs/architecture/agent.md` "Task RPC piggyback" 절 + ADR 0002 |
| JSON Export v3 | `exports.py` | `query_service.py` | 정제 inventory JSON (`schema_version: v3`) | `docs/architecture/inventory-export.md` |
| 보고서 양식 A/B | `pages.py` (`/servers/report`) | `query_service.get_report` + `diagnostic_service.get_many_latest_server` | HTML SSR (양식 A KPI / 양식 B 15컬럼) | 본 문서 "보고서 양식 A/B" |

## 서버 발견 (Discovery)

추후 에이전트 자동 배포 워크플로우의 1단계 도달성 검사. 운영자가 IP를 입력하면 HTTP probe로 "이 IP가 네트워크상 살아있나" 확인.

흐름:
```
POST /api/v1/discovery/probe
  body: {ip, port=80, scheme="http"}
  -> ipaddress.ip_address(v) 파싱 검증 (IPv4/IPv6) -> 422 차단
  -> httpx.AsyncClient (timeout=5s) GET {scheme}://{ip}:{port}/
  -> {reachable: bool, status_code: int | None, latency_ms: float}
```

설계 결정:
- 타임아웃 5초 — 폐쇄망 LAN 가정. 폐쇄망에서 HTTP 80/443은 보통 열려있어 가벼운 도달성 검사로 적합.
- SSRF 방지(localhost·메타데이터 IP 차단) 미적용 — 폐쇄망 가정상 운영자 의도 입력으로 간주.
- ICMP 미사용 — raw socket 권한 필요라 회피.
- fail-open — HTTP 도달은 SSH 도달을 의미하지 않음. 1차 필터일 뿐 (#F6).
- 추후 SSH credential 등록 + ansible-playbook 실행 흐름이 본 단계 위에 얹힘.

## ZConverter Install task

운영자가 등록된 서버에 변환 도구 설치 명령을 발행. RPC piggyback 패턴 (ADR 0002).

흐름:
```
POST /api/v1/tasks/install
  body: {target_public_ids: [...], source_host}
  -> TaskService.create_install_tasks (트랜잭션 경계)
    -> resolve_server_ids -> DB INSERT (tasks 행, status='pending')
    -> Redis SET task:pending:{machine_id} (hot path 캐시, TTL 24h)
  -> 응답 [{target_public_id, task_public_id}, ...]

agent 측 (별도):
  agent가 다음 server.metrics 발행 시 reply_to·correlation_id 명시
    -> consumer가 metrics 처리 후 Redis EXISTS task:pending:{machine_id}
      -> 있으면 reply publish (amq.rabbitmq.reply-to)
  agent가 source_host에서 install bundle fetch:
    -> curl http://{source_host}/zconverter.tar.gz   # source_host=엔진이면 본 엔진 self-host (payloads.py)
    -> tar -xzf zconverter.tar.gz                    # install.sh가 mode=0o755로 풀림
    -> bash install.sh                               # 실제 실행
  agent가 task.result 큐로 결과 보고
    -> consumer가 DB UPDATE (status·result_message·completed_at) + Redis pending DEL
```

Install bundle 호스팅:
- `web/routers/payloads.py` `GET /zconverter.tar.gz` — 본 엔진 self-host. 운영자가 `source_host=<engine-fqdn>:8000` 입력 시 활용.
- in-memory tar.gz 생성. `install.sh` 내용은 코드 안 `_INSTALL_SCRIPT` 상수 + `mode=0o755` 메타 박힘.
- 외부 mirror 호스팅도 가능 — agent path 계약(`/zconverter.tar.gz`) 동일하면 source_host만 외부로.
- 상세는 `docs/architecture/agent.md` "Install bundle endpoint" 절.

설계 결정:
- 부분 UNIQUE `(target_server_id) WHERE status='pending'` — 운영자 더블클릭 방어. `IntegrityError` -> `_DuplicatePending` -> 409 (#C1, F3).
- Latency = metrics 주기 (즉시 push 아님 — 별도 polling endpoint·task queue 안 만드는 대가). 즉시성 요구 시 ADR 0002 전환 경로 참조.
- Redis는 hot path 캐시 — 99% no-op 응답을 1ms 미만으로 흡수. Redis 장애 시 silent skip (다음 주기 재시도, #C3 fail-open).

상세 메시지 스키마·task_type 카탈로그: `docs/architecture/agent.md` "Task RPC piggyback" 절.

## JSON Export v3

선택 서버 N대의 정제 inventory + 사용량 통계(p95·peak)를 OpenStack/Terraform/SDK 입력용 표준 JSON으로 다운로드.

흐름:
```
POST /api/v1/exports/inventory
  body: {target_public_ids: [...], period_days=7}
  -> resolve_server_ids -> 누락 시 422
  -> query_service.get_inventory_export(server_ids, period_days)
  -> 정제 JSON (envelope + servers[])
```

스키마 단일 진실: `docs/architecture/inventory-export.md` v3.

주요 envelope 필드:
- `engine_id` / `schema_version: "v3"` / `schema_doc` — reproducibility
- `period_window: {days, start, end}` — F11 평가 윈도우 일관성
- `size_class_guide` — recommended_size_class -> 자동화 도구가 자기 도메인 instance type 매핑할 때 참고용
- I/O p95/peak·recommended_size_class 객체화·services.listeners (proto·address) — listen_ports inventory 매칭

브라우저 download — 서버에서 파일 생성 안 함 (stateless). 1000건 batch 상한.

## 보고서 양식 A/B

같은 endpoint·같은 SQL·`view` 파라미터로 양식 분기.

흐름:
```
GET /servers/report?ids=p1,p2,...&period_days=14&view=customer|engineer
  -> resolve_server_ids -> 누락 시 404
  -> query_service.get_report(server_ids, period_days)   # USE Method 통계 1회 SQL
  -> diagnostic_service.get_many_latest_server(public_ids, "14d")  # batch fetch (N+1 회피 #C5)
  -> servers/report.html 렌더 (view에 따라 템플릿 안 분기)
```

양식 차이:

| 항목 | 양식 A (customer) | 양식 B (engineer) |
|------|-------------------|-------------------|
| 목적 | 고객 제출용 KPI + 위험도 요약 | 엔지니어 검토용 정량 표 |
| 구성 | KPI 헤더 + 분류 분포 + 위험 신호 | 15컬럼 정량 표 + 자동 진단 텍스트 |
| 행 단위 | (없음 — 환경 요약) | 서버 1대 = 1 행 |
| 표시 정밀도 | 분류 라벨 + 색상 | p95·peak 숫자 + 셀 안 multi-line |
| 인쇄 | navbar·검색폼·버튼 hide (`.no-print`) | 동일 + `engineer-table` 압축 폰트 |
| 시간 축 | `period_days` (기본 14, F11 윈도우) | 동일 |

분기 위치: `web/templates/servers/report.html` `{% if view == "customer" %} ... {% endif %}` / `{% if view == "engineer" %} ... {% endif %}`. CSS는 `.engineer-table`이 양식 B 전용.

AI 진단 카드: 양쪽 양식 모두 server detail card + report 행에 latest succeeded 진단 1건씩 표시 (`to_panel_payload`). 진단 발행은 라우터 안에서 안 함 — 카드는 정보 표시만, 발행은 사용자가 "AI 진단" 모달 트리거.

설계 결정:
- 한 endpoint·한 SQL·한 템플릿 — 양식별 데이터 fetch 분기 없음. URL `?view=` 토글만으로 시각 차이.
- 컨설턴트가 브라우저 인쇄 -> PDF/PPT 캡처. 백엔드 PDF export 미도입 (`docs/architecture/web/static-assets.md` "report.html print CSS" 참조).
- `period_days`는 `Query(14, ge=1, le=90)` 명시 — 라우터 단일 검증 (F3). 기본값 14는 F11 평가 윈도우 단일 진실(`recommendation.WINDOW_DAYS`).

USE Method 분류 임계값 출처(AWS Compute Optimizer / Azure Advisor / GCP Recommender / Kleinrock 큐잉 / Linux page cache): `docs/architecture/web/services.md` "Recommendation 분류" 절.

## 관련 문서

- `docs/architecture/inventory-export.md` v3 — JSON Export 스키마·정제 원칙
- `docs/architecture/agent.md` "Task RPC piggyback" — Install task 메시지 흐름
- `docs/architecture/web/routers.md` — 라우터 모듈 분리
- `docs/architecture/web/services.md` — `query_service`·`task_service`·`diagnostic_service` 책임
- `docs/architecture/web/static-assets.md` — report.html print CSS
- ADR 0002 — Task RPC piggyback 채택 사유
- ADR 0003 — USE Method 임계값·LLM 모델 선택
- ADR 0004 — AI 진단 워커 (보고서 AI 진단 카드 데이터 원천)
