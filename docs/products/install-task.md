# Install Task

본 문서는 Install task 산출물(엔진이 원격 호스트에 도구 설치 명령을 발행하는 워크플로)의 존재 의의·구현 의도·근거를 정리한다. 메시지 schema·실행 흐름·기술 세부는 `docs/architecture/agent.md` "task.install" / "task.result" 절 + `docs/architecture/rabbitmq.md` 별도. ADR 0007 (Task 별도 큐 모델, 0002 supersede).

## 위치

- UI 진입점: 대시보드 list 페이지에서 N대 선택 → "Install" 모달(ZDM 서버 IP·관리자 계정 입력) → 발행. 또는 server detail "최근 작업" timeline에서 진행 추적
- 발행 경로: 사용자 트리거 (스케줄러 자동 발행 없음 — 운영자 명시 결정만)
- 산출물 형태: 각 워커 VM의 worker가 ZDM 본체 패키지 다운로드 + `install.sh -s ZDM_IP -u ZDM_USER` 실행 + 결과를 엔진으로 보고. Task row 6 컬럼 UPDATE (status·exit_code·duration_ms·stdout_tail·stderr_tail·failure_reason)
- OS 범위: Linux 만. Windows 호스트는 본 산출물 범위 밖 (별도 산출물 도입 시 ADR)
- 가시성: list "최근 작업" column (success/failure/pending badge) + detail timeline + `GET /api/v1/tasks/{id}` / `GET /api/v1/tasks?server_public_id=...&cursor=...`

## 존재 의의

본 엔진이 모니터링·진단을 넘어 운영자가 선택한 서버에 직접 설치 작업을 발행할 수 있게 하는 산출물. 다음 질문에 답한다.

질문 1: "이 서버 N대에 ZConverter 변환 도구를 설치하려면?"

기존 패턴은 각 서버에 SSH 접속 후 수동 install. N대 ↑ 시 운영 부담. 본 엔진은 list에서 N대 선택 → Install 버튼 한 번으로 각 워커 VM의 agent worker가 ZDM 본체 패키지를 fetch·실행 → 결과를 자동 수집·노출. 운영자가 SSH·ansible playbook 없이 web UI에서 끝.

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
  2. download.url(`http://{ZDM_IP}{ZDM_PACKAGE_PATH}`) fetch (sha256·size 검증, host whitelist 통과)
  3. tar 추출 후 install.script 경로(`zconverter_install_source/install.sh`) exec
     — args=[-s, ZDM_IP, -u, ZDM_USER] 전달, timeout INSTALL_TIMEOUT_SEC
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

ZDM 패키지 contract:
- `ZDM_PACKAGE_PATH`·`ZDM_PACKAGE_SCRIPT` env 가 ZDM 측 본체 패키지 layout 과 일치해야 함. sha256/size 는 엔진이 publish 직전 ZDM 에서 HEAD + (cache miss 시) GET full 로 동적 산출 (`HttpZdmPackageResolver`). ZDM 패키지 갱신 시 ETag 자동 변경으로 cache invalidation — 운영자 개입 0.
- 메타 fetch 실패 (ZDM 도달 불가·HEAD non-200·size mismatch) 시 install 발행 503 차단.
- agent 측 host whitelist (`WORKER_DOWNLOAD_ALLOWED_HOSTS`) 에 운영자가 박을 ZDM host 가 사전 등록되어야 함. agent config 는 deploy 시점 고정 — 새 host 도입 시 agent 재배포 필요.

## 한계

1. task_type이 install 1종 — 다른 작업(uninstall·rollback·재시작 등) 미지원. 향후 task_type enum 확장 시 별도 결정.
2. Linux 만 지원 — Windows 호스트는 발행 대상에서 제외 (별도 산출물 도입 시 ADR).
3. 워커 측 중복 발행 차단 — 부분 UNIQUE `uq_tasks_pending_per_server_type` (status=pending 한 서버당 1건)이 DB 레벨 차단. 다만 발행 직후 cleanup 전엔 같은 서버에 신규 task 발행 불가 — 운영 ↑.
4. ZDM 패키지 매 publish 마다 HEAD 1 회 (cache hit) 또는 GET full 44MB (cache miss). 같은 LAN 가정에 1~2s. 다른 네트워크면 ZDM_META_TOTAL_TIMEOUT_SEC 안에 끝나야 503 회피.
5. ZDM 좌표는 모달 일괄 입력 — N대 호스트가 서로 다른 ZDM 서버를 가리키는 시나리오 미지원. 발행 단위로 동일 ZDM IP/User 적용.
6. stdout/stderr UTF-8 가정 — 호스트 OS locale에 따라 깨짐 가능. agent worker가 binary으로 받고 latin-1 fallback 적용.

## 관련 문서·코드

- ADR 0007 — Task 별도 큐 모델
- `docs/architecture/agent.md` "task.install" / "task.result" 절 — 메시지 schema·필드 카탈로그
- `docs/architecture/rabbitmq.md` — exchange·queue·routing key 토폴로지
- `src/assessment_engine/web/services/task_service.py` — Task 발행
- `src/assessment_engine/web/routers/tasks.py` — POST /api/v1/tasks · GET 조회
- `src/assessment_engine/consumer/handlers/` — task.result 핸들러 (6 컬럼 UPDATE)
- `src/assessment_engine/web/static/js/pages/list.js` — list "최근 작업" column polling
- `src/assessment_engine/web/templates/base.html` — task modal (stdout/stderr 확장)
