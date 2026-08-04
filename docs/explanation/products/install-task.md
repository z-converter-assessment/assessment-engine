# Install Task

본 문서는 Install task 산출물(엔진이 원격 호스트에 도구 설치 명령을 발행하는 워크플로)의 존재 의의·구현 의도·근거를 정리한다. 메시지 schema·실행 흐름·기술 세부는 `docs/reference/contracts/agent-data.md` "J1. task.result" 절 + `docs/reference/rabbitmq.md` 별도. Task 별도 큐 모델.

## 위치

- UI 진입점: 대시보드 list 페이지에서 N대 선택 → "Install" 모달(ZDM 서버 IP·관리자 계정 입력) → 발행. 또는 server detail "ZDM" 카드에서 단건 발행 + 이력 추적
- 발행 경로: 사용자 트리거 (스케줄러 자동 발행 없음 — 운영자 명시 결정만)
- 산출물 형태: 각 워커 VM의 worker가 ZDM 본체 패키지 다운로드 + 설치 실행(args `-s ZDM_IP -u ZDM_USER`) + 결과를 엔진으로 보고. Task row 9 컬럼 UPDATE (항목별 의미는 아래 "완료 시점" 표)
- OS 범위: Linux(.tar.gz 추출 후 install script exec) · Windows(.exe 직접 실행) 2 계열. `os_family` 별로 ZDM 패키지 path·install type 이 갈리고, 그 외 family 는 발행 거부(503)
- 가시성: list "ZDM Install" column (success/failure/pending badge) + detail "ZDM" 카드 표 + 단건 조회 `GET /api/tasks/{id}` (JSON) / detail HTML fragment `GET /api/tasks/{id}/detail` (task-modal 본문)

## 존재 의의

본 엔진이 모니터링·진단을 넘어 운영자가 선택한 서버에 직접 설치 작업을 발행할 수 있게 하는 산출물. 다음 질문에 답한다.

질문 1: "이 서버 N대에 ZConverter 변환 도구를 설치하려면?"

기존 패턴은 각 서버에 SSH 접속 후 수동 install. N대 증가 시 운영 부담. 본 엔진은 list에서 N대 선택 → Install 버튼 한 번으로 각 워커 VM의 agent worker가 ZDM 본체 패키지를 fetch·실행 → 결과를 자동 수집·노출. 운영자가 SSH·ansible playbook 없이 web UI에서 끝.

질문 2: "어떤 서버에 설치 성공·실패했나? 실패 사유는?"

발행된 task는 `Task` row에 영속 — 워커 회신이 pending 을 success/failure 로 바꾸며 9 컬럼을 UPDATE 한다. list "ZDM Install" column에 badge로 노출. 클릭 시 modal에서 stdout/stderr 마지막 4 KB까지 확인 가능 — 실패 디버깅 즉시.

질문 3: "발행한 task의 진행 상황은?"

발행 직후 list page가 polling 시작 → status가 pending → success/failure로 전이될 때 badge 자동 갱신. detail page "ZDM" 카드 표에서 시계열 순서로 task 이력 추적, 행별 "상세" 버튼 클릭 시 modal로 stdout/stderr 확인(행 전체가 아닌 버튼만 클릭 타겟 — `data-task-id` 위임).

## 산출 정보

발행 시점:

| 항목 | 내용 | source |
|------|------|--------|
| target_server_id | 발행 대상 서버 (FK) | 사용자 선택 N대 |
| task_type | `zconverter_install` (현재 1종, 표시는 "ZConverter Install"로 매핑) | 고정 |
| status 초기 | `pending` | INSERT 시점 |
| created_at | 발행 시각 | 자동 |

완료 시점 (워커가 `task.result` publish):

| 항목 | 내용 | 의미 |
|------|------|------|
| status | success / failure | `task_policy`(설치 실증 신호) 우선 — True=success · False=failure. 미보고(null)면 exit_code + OS별 성공코드 allowlist 폴백 |
| task_policy | bool 또는 null | agent 가 데몬 기동·등록을 확인해 발행하는 실증 신호. raw 보존 |
| exit_code | int 또는 null | 설치 스크립트 종료 코드. 정책 보정과 무관하게 raw 보존(감사용) |
| signal_no | int 또는 null | 시그널 종료 번호. exit_code 와 상호배타, Windows 는 항상 null |
| duration_ms | int | 다운로드 + 실행 wall-clock |
| stdout_tail | 4096 byte (4 KB) — agent `exec.c` cap | 설치 스크립트 표준 출력 끝부분 |
| stderr_tail | 4096 byte (4 KB) — agent `exec.c` cap | 설치 스크립트 표준 오류 끝부분 |
| failure_reason | nullable enum | url_not_allowed / download_failed / sha256_mismatch / extract_failed / script_not_found / script_failed / script_timeout / insufficient_disk / internal_error / already_done / unsupported_install_type / install_unverified / timeout (표시 라벨은 `mappers/task.py` `_FAILURE_REASON_LABEL`) |
| completed_at | UTC datetime | 워커가 publish 시각 |

마감 시점 (배달·회신 없음):

| 항목 | 내용 | 의미 |
|------|------|------|
| status | failure | 마감 경과 pending 의 종결 상태 |
| failure_reason | `timeout` | 엔진 마감 만료 (agent 미발행 사유) |
| deadline_at | 발행 시각 + `install_task_deadline_sec`(기본 3600) | broker 큐 `x-message-ttl` 과 같은 창 — 엔진 timeout 선언 시점 == 미배달 메시지 만료 시점이라 지연 실행 없음 |

전이 주체는 전용 워커 프로세스의 install reaper 루프 — emit 과 무관하게 주기적으로 경과 pending 을 전역 정리한다. 발행 경로도 INSERT 직전 대상 서버분만 같은 전이를 수행.

## 메시지 흐름 (요약)

```
사용자 list 선택 → "Install" 모달 → POST /api/tasks/install
  v
engine web:
  1. Task INSERT (status=pending)
  2. task.install.<agent_id> publish to assessment.tasks exchange
  3. agent.tasks.<agent_id> 큐로 routing
  v
워커 VM의 agent worker:
  1. agent.tasks.<agent_id> consume
  2. download.url(`http://{ZDM_IP}{ZDM_PACKAGE_PATH}`) fetch (sha256·size 검증, host whitelist 통과)
  3. install.type 분기:
     - shell (Linux .tar.gz): tar 추출 후 install.script 경로 exec
     - direct_exec (Windows .exe): 다운로드 파일 직접 실행
     자기 OS 가 아닌 type 수신 시 unsupported_install_type reject
     — args=[-s, ZDM_IP, -u, ZDM_USER] OS 무관 동일, timeout INSTALL_TIMEOUT_SEC
  4. task.result publish (worker.result 큐)
  v
engine consumer:
  1. worker.result consume
  2. Task row 9 컬럼 UPDATE
  v
list page polling → badge 자동 갱신 (success/failure)
```

자세한 메시지 schema: `docs/reference/contracts/agent-data.md`.

## 의사결정 근거

Task 별도 exchange:
- `assessment.tasks` exchange (server.* exchange와 분리)
- 머신별 queue `agent.tasks.<agent_id>` — 워커가 자기 머신 task만 consume
- 결과는 단일 `worker.result` 큐로 통합 — engine consumer가 routing 무관 처리

4 KB tail 한정 근거:
- 전체 stdout/stderr 저장은 DB 비대화
- 끝부분 4 KB 는 디버깅에 충분 (에러 메시지·exit 직전 로그). ZConverter Install 실패 사례 분석 결과 평균 디버깅 정보 < 4 KB
- agent `exec.c` 의 `out_storage[4096]` / `err_storage[4096]` circular tail buffer 단일 진실. 엔진 Inbound DTO `max_length=8192` 는 over-provision (agent minor bump 흡수)

ZDM 패키지 contract:
- ZDM 패키지 layout 상수(OS별 path·install script, 키 카탈로그는 `docs/reference/contracts/env.md`)가 ZDM 측 본체 패키지 layout 과 일치해야 함. sha256/size 는 엔진이 publish 직전 ZDM 에서 HEAD + (cache miss 시) GET full 로 동적 산출 (`HttpZdmPackageResolver`). ZDM 패키지 갱신 시 ETag 자동 변경으로 cache invalidation — 운영자 개입 0.
- 메타 fetch 실패 (ZDM 도달 불가·HEAD non-200·size mismatch) 시 install 발행 503 차단.
- agent 측 host whitelist (`WORKER_DOWNLOAD_ALLOWED_HOSTS`) 에 운영자가 박을 ZDM host 가 사전 등록되어야 함. agent config 는 deploy 시점 고정 — 새 host 도입 시 agent 재배포 필요.

## 한계

1. task_type이 zconverter_install 1종 — 다른 작업(uninstall·rollback·재시작 등) 미지원. 표시 라벨 매핑은 `mappers/task.py`의 `_TASK_TYPE_LABEL`(미지 값은 raw 그대로 폴백).
2. 중복 발행 차단은 부분 UNIQUE `uq_tasks_pending_per_server_type` (status=pending 한 서버당 1건). 마감(`deadline_at`)이 지나지 않은 진행 중 task 를 가진 서버가 하나라도 섞이면 all-or-nothing 으로 batch 전체가 취소된다(409). 마감 경과분은 발행 직전 정리·reaper 전역 정리 양쪽이 해소.
3. ZDM 패키지 매 publish 마다 HEAD 1 회 (cache hit) 또는 GET full 44MB (cache miss). 같은 LAN 가정에 1~2s. 다른 네트워크면 ZDM_META_TOTAL_TIMEOUT_SEC 안에 끝나야 503 회피.
4. ZDM 좌표는 모달 일괄 입력 — N대 호스트가 서로 다른 ZDM 서버를 가리키는 시나리오 미지원. 발행 단위로 동일 ZDM IP/User 적용.
5. stdout/stderr UTF-8 가정 — 호스트 OS locale에 따라 깨짐 가능. agent worker가 binary으로 받고 latin-1 fallback 적용.

## 관련 문서·코드

- `docs/reference/contracts/agent-data.md` "J1. task.result" 절 — 메시지 schema·필드 카탈로그
- `docs/reference/rabbitmq.md` — exchange·queue·routing key 토폴로지
- `src/assessment_engine/web/services/task_service.py` — Task 발행
- `src/assessment_engine/web/routers/tasks.py` — POST /api/tasks/install · GET 조회
- `src/assessment_engine/task_policy.py` — success/failure 판정 정책 (task_policy 우선, exit_code allowlist 폴백)
- `src/assessment_engine/worker/task_reaper.py` — 마감 경과 pending 전역 timeout 전이
- `src/assessment_engine/consumer/handlers/` — task.result 핸들러 (9 컬럼 UPDATE)
- `src/assessment_engine/web/static/js/pages/list-table.js` — list "ZDM Install" column polling
- `src/assessment_engine/web/templates/base.html` — task modal (stdout/stderr 확장)
