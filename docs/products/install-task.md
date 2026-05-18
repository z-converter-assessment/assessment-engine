# Install Task

본 문서는 Install task 산출물(엔진이 원격 호스트에 도구 설치 명령을 발행하는 워크플로)의 존재 의의·구현 의도·근거를 정리한다. 메시지 schema·실행 흐름·기술 세부는 `docs/architecture/agent.md` "task.install" / "task.result" 절 + `docs/architecture/rabbitmq.md` 별도. ADR 0007 (Task 별도 큐 모델, 0002 supersede).

## 위치

- UI 진입점: 대시보드 list 페이지에서 N대 선택 → "Install" 모달(ZDM 서버 IP·관리자 계정 입력) → 발행. 또는 server detail "최근 작업" timeline에서 진행 추적
- 발행 경로: 사용자 트리거 (스케줄러 자동 발행 없음 — 운영자 명시 결정만)
- 산출물 형태: 각 워커 VM의 worker가 install bundle 다운로드 + OS별 스크립트(Linux `install.sh` / Windows `install.ps1`) 를 `-s ZDM_IP -u ZDM_USER` 인자로 실행 + 결과를 엔진으로 보고. Task row 6 컬럼 UPDATE (status·exit_code·duration_ms·stdout_tail·stderr_tail·failure_reason)
- OS 분기: 호스트 `inventory.os_id` 기준 엔진이 자동 (`_is_windows()` — 'windows' 키워드/'win' prefix 매칭). 발행 측이 OS 추가 명시 불필요
- 가시성: list "최근 작업" column (success/failure/pending badge) + detail timeline + `GET /api/v1/tasks/{id}` / `GET /api/v1/tasks?server_public_id=...&cursor=...`

## 존재 의의

본 엔진이 모니터링·진단을 넘어 운영자가 선택한 서버에 직접 설치 작업을 발행할 수 있게 하는 산출물. 다음 질문에 답한다.

질문 1: "이 서버 N대에 ZConverter 변환 도구를 설치하려면?"

기존 패턴은 각 서버에 SSH 접속 후 수동 install. N대 ↑ 시 운영 부담. 본 엔진은 list에서 N대 선택 → Install 버튼 한 번으로 각 워커 VM의 agent worker가 install bundle을 fetch·실행 → 결과를 자동 수집·노출. 운영자가 SSH·ansible playbook 없이 web UI에서 끝.

질문 2: "어떤 서버에 설치 성공·실패했나? 실패 사유는?"

발행된 task는 `Task` row에 영속 — `status` (success/failure/pending) + `exit_code` + `duration_ms` + `stdout_tail` + `stderr_tail` + `failure_reason` 6 컬럼 UPDATE. list "최근 작업" column에 badge로 노출. 클릭 시 modal에서 stdout/stderr 마지막 8192 byte까지 확인 가능 — 실패 디버깅 즉시.

질문 3: "발행한 task의 진행 상황은?"

발행 직후 list page가 polling 시작 → status가 pending → success/failure로 전이될 때 badge 자동 갱신. detail page "최근 작업" timeline에서 시계열 순서로 task 이력 추적.

## 산출 정보

발행 시점:

| 항목 | 내용 | source |
|------|------|--------|
| target_server_id | 발행 대상 서버 (FK) | 사용자 선택 N대 |
| task_type | `install` (현재 1종) | 고정 |
| status 초기 | `pending` | INSERT 시점 |
| created_at | 발행 시각 | 자동 |

완료 시점 (워커가 `task.result` publish):

| 항목 | 내용 | 의미 |
|------|------|------|
| status | success / failure | exit_code=0 → success, 그 외 → failure |
| exit_code | int 또는 null | install.sh 종료 코드 |
| duration_ms | int | 다운로드 + 실행 wall-clock |
| stdout_tail | 8192 byte max | install.sh 표준 출력 끝부분 |
| stderr_tail | 8192 byte max | install.sh 표준 오류 끝부분 |
| failure_reason | nullable enum | url_not_allowed / download_failed / extract_failed / exec_failed / timeout 등 |
| completed_at | UTC datetime | 워커가 publish 시각 |

## 메시지 흐름 (요약)

```
사용자 list 선택 → "Install" 모달 → POST /api/v1/tasks
  ↓
engine web:
  1. Task INSERT (status=pending)
  2. task.install.<machine_id> publish to assessment.tasks exchange
  3. agent.tasks.<machine_id> 큐로 routing
  ↓
워커 VM의 agent worker:
  1. agent.tasks.<machine_id> consume
  2. download.url fetch (sha256·size 검증) — bundle 안에 install.sh + install.ps1 두 스크립트 포함
  3. install.script 필드 지정 스크립트 exec (Linux는 install.sh, Windows는 install.ps1) — args=[-s, ZDM_IP, -u, ZDM_USER] 전달, timeout INSTALL_TIMEOUT_SEC
  4. task.result publish (worker.result 큐)
  ↓
engine consumer:
  1. worker.result consume
  2. Task row 6 컬럼 UPDATE
  ↓
list page polling → badge 자동 갱신 (success/failure)
```

자세한 메시지 schema: `docs/architecture/agent.md`.

## 의사결정 근거

ADR 0007 — Task 별도 exchange:
- `assessment.tasks` exchange (server.* exchange와 분리)
- 머신별 queue `agent.tasks.<machine_id>` — 워커가 자기 머신 task만 consume
- 결과는 단일 `worker.result` 큐로 통합 — engine consumer가 routing 무관 처리

8192 byte tail 한정 근거:
- 전체 stdout/stderr 저장은 DB 비대화
- 끝부분 8192는 디버깅에 충분 (에러 메시지·exit 직전 로그)
- ZConverter Install 실패 사례 분석 결과 평균 디버깅 정보 < 4KB

agent v3.2 HTTPS-only 정책 한계 (dev):
- agent worker download.c가 `https://` prefix만 허용 — dev plain HTTP install bundle endpoint에서 `failure_reason="url_not_allowed"` reject
- dev에서는 wire format 검증(failure 경로)까지만 가능. success 경로는 agent 측 호환성 작업 후 활성화 (ADR 0009)
- prod에선 외부 ingress nginx 등이 TLS termination — install bundle이 HTTPS로 제공되면 success 정상 동작

## 한계

1. task_type이 install 1종 — 다른 작업(uninstall·rollback·재시작 등) 미지원. 향후 task_type enum 확장 시 별도 결정.
2. dev success 경로 미검증 — agent v3.2 HTTPS-only 정책 + dev plain HTTP 충돌(ADR 0009). agent worker 호환성 작업(WORKER_ALLOW_HTTP toggle 또는 nginx ingress sidecar) 후 활성화 가능.
3. 워커 측 중복 발행 차단 — 부분 UNIQUE `uq_tasks_pending_per_server_type` (status=pending 한 서버당 1건)이 DB 레벨 차단. 다만 발행 직후 cleanup 전엔 같은 서버에 신규 task 발행 불가 — 운영 ↑.
4. install bundle 단일 endpoint `/zconverter.tar.gz` — 버전·환경별 분기 없음. 다양한 task_type·버전 도입 시 endpoint 분기 또는 query parameter 필요. (OS별 스크립트는 단일 bundle 안 두 파일로 처리 — agent 가 install.script 필드로 선택)
6. ZDM 좌표는 모달 일괄 입력 — N대 호스트가 서로 다른 ZDM 서버를 가리키는 시나리오 미지원. 발행 단위로 동일 ZDM IP/User 적용.
7. OS 분기는 `inventory.os_id` 기준 휴리스틱 (`_is_windows()`) — agent 가 보내는 정확한 Windows os_id 값 명세 미확정. 추후 agent 측 명세 확정 시 본 함수에 정합.
5. stdout/stderr UTF-8 가정 — 호스트 OS locale에 따라 깨짐 가능. agent worker가 binary으로 받고 latin-1 fallback 적용.

## 관련 문서·코드

- ADR 0007 — Task 별도 큐 모델
- ADR 0009 — dev plain HTTP 복귀 (Install success 경로 limitation 사유)
- `docs/architecture/agent.md` "task.install" / "task.result" 절 — 메시지 schema·필드 카탈로그
- `docs/architecture/rabbitmq.md` — exchange·queue·routing key 토폴로지
- `src/assessment_engine/web/services/task_service.py` — Task 발행
- `src/assessment_engine/web/routers/tasks.py` — POST /api/v1/tasks · GET 조회
- `src/assessment_engine/consumer/handler.py` — task.result 핸들러 (6 컬럼 UPDATE)
- `src/assessment_engine/web/static/js/pages/list.js` — list "최근 작업" column polling
- `src/assessment_engine/web/templates/base.html` — task modal (stdout/stderr 확장)
