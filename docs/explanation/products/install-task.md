# Install Task

엔진이 원격 호스트에 ZConverter 설치 명령을 발행하는 산출물의 존재 의의·구현 의도·근거를 다룬다. 메시지
schema·필드 카탈로그는 `docs/reference/contracts/agent-data.md` "J1. task.result" 절, exchange·큐·routing key
토폴로지는 `docs/reference/rabbitmq.md` 가 갖는다.

본문의 "에이전트"는 원격 호스트에서 명령을 실행하는 쪽이고, 엔진 안의 전용 워커 프로세스와 다른 주체다.

## 위치

- UI 진입점 — 서버 목록에서 N대 선택 후 "Install" 모달(ZDM 서버 IP·관리자 계정 입력)로 발행. 서버 상세 "ZDM"
  카드에서 단건 발행 + 이력 추적
- 발행 경로 — 운영자 트리거 단일. 스케줄러 자동 발행은 없다
- 산출물 형태 — 에이전트가 ZDM 본체 패키지를 다운로드하고 설치를 실행(args `-s ZDM_IP -u ZDM_USER`)한 뒤 결과를
  엔진으로 보고
- OS 범위 — Linux(.tar.gz 추출 후 install script exec) · Windows(.exe 직접 실행) 2 계열. `os_family` 별로 ZDM
  패키지 path·install type 이 갈리고, 그 외 family 는 발행 거부(503)
- 가시성 — 목록 "ZDM Install" 칼럼(success/failure/pending 배지) + 상세 "ZDM" 카드 표 + 단건 조회
  `GET /api/tasks/{id}`(JSON) / HTML fragment `GET /api/tasks/{id}/detail`(task-modal 본문)

## 존재 의의

본 엔진이 모니터링·진단을 넘어 운영자가 선택한 서버에 직접 설치 작업을 발행할 수 있게 하는 산출물이다. 다음
질문에 답한다.

질문 1: "이 서버 N대에 ZConverter 변환 도구를 설치하려면?"

기존 패턴은 각 서버에 SSH 접속 후 수동 install 이라 N대가 늘수록 운영 부담이 선형으로 커진다. 본 엔진은
목록에서 N대 선택 후 Install 버튼 한 번으로 각 호스트의 에이전트가 ZDM 본체 패키지를 fetch·실행하고 결과를
자동 수집·노출한다. 운영자가 SSH·ansible playbook 없이 web UI 에서 끝낸다.

질문 2: "어떤 서버에 설치 성공·실패했나? 실패 사유는?"

발행된 task 는 `tasks` 행에 영속해 배지로 노출되고, 클릭하면 모달에서 stdout/stderr tail 을 확인할 수 있어
실패 디버깅이 즉시 가능하다.

질문 3: "발행한 task 의 진행 상황은?"

발행 직후 목록 페이지가 polling 을 시작해 종결 상태 도달 시 배지를 자동 갱신한다. 상세 페이지 "ZDM" 카드 표는
시계열 순서로 이력을 쌓는다.

## 상태 전이

`pending` 으로 INSERT 되어 종결 상태 둘 중 하나로 간다.

에이전트 회신 도착 시 — `task_policy`(에이전트가 데몬 기동·등록을 확인해 발행하는 실증 신호)가 성패를 정하고,
미보고(null)면 `exit_code` + OS별 성공코드 allowlist 로 폴백한다. 보정 결과와 별개로 원본 종료 신호는 `tasks`
행에 raw 로 남긴다 — 왜 그렇게 판정됐는지가 사후에 감사 가능해야 한다.

마감 경과 시 — status `failure` + failure_reason `timeout`(엔진 마감 만료, 에이전트 미발행). 마감 시각
`deadline_at` 은 발행 시각 + `INSTALL_TASK_DEADLINE_SEC`(`docs/reference/contracts/env.md`)이고, broker 큐의
`x-message-ttl` 이 같은 창이라 엔진이 timeout 을 선언한 시점에 미배달 메시지도 함께 만료한다. 지연 실행되는
zombie task 가 생기지 않는다.

전이 주체는 전용 워커 프로세스의 install reaper 루프다 — emit 과 무관하게 주기적으로 경과 pending 을 전역
정리한다. 발행 경로도 INSERT 직전 대상 서버분만 같은 전이를 수행한다.

`failure_reason` 값 집합은 `docs/reference/contracts/agent-data.md` J1 절이 갖는다.

## 메시지 흐름 (요약)

```
user selects hosts -> "Install" modal -> POST /api/tasks/install
  v
engine web:
  1. INSERT task (status=pending)
  2. publish task.install.<agent_id> to assessment.tasks exchange
  3. routed to queue agent.tasks.<agent_id>
  v
agent on remote host:
  1. consume agent.tasks.<agent_id>
  2. fetch download.url (sha256/size verify, host whitelist)
  3. dispatch on install.type:
       shell       (Linux .tar.gz)  -> extract, exec install.script
       direct_exec (Windows .exe)   -> run downloaded file directly
       foreign type -> reject with unsupported_install_type
     args=[-s ZDM_IP, -u ZDM_USER] identical on both OS
  4. publish task.result
  v
engine consumer:
  1. consume worker.result
  2. UPDATE task row with the result fields
  v
list page polling -> badge updated (success/failure)
```

## 의사결정 근거

Task 를 수집 exchange 와 분리한 이유 — 수집은 fan-in(전 호스트 -> 엔진)인데 명령은 fan-out(엔진 -> 지정 호스트
1대)이라 라우팅 방향이 반대다. 한 exchange 에 섞으면 명령 배달이 수집 큐 정책(TTL·상한)에 묶인다.

stdout/stderr 를 tail 만 보관하는 이유 — 전체 출력을 저장하면 DB 가 비대해지고, 디버깅에 필요한 정보(에러
메시지·exit 직전 로그)는 끝부분에 몰린다. 실제 저장 상한은 에이전트의 circular tail buffer 가 정하고 엔진
Inbound DTO 는 그보다 넉넉히 잡아 에이전트 minor bump 를 흡수한다.

sha256/size 를 상수로 박지 않고 매번 산출하는 이유 — 엔진이 publish 직전 ZDM 에서 HEAD + (cache miss 시) GET
full 로 얻는다(`HttpZdmPackageResolver`). 패키지가 갱신되면 ETag 가 바뀌어 cache 가 자동 무효화되므로 운영자가
체크섬을 손으로 갱신할 일이 없다. 메타 fetch 가 실패하면(ZDM 도달 불가·HEAD non-200·size mismatch) 발행을 503
으로 차단한다 — sha256 없이 발행하면 에이전트가 검증 없이 설치한다.

패키지 layout 상수(OS별 path·install script)는 ZDM 측 본체 패키지 layout 과 일치해야 하고, 에이전트 측 host
whitelist 에 ZDM host 가 사전 등록되어야 한다. 에이전트 config 는 배포 시점에 고정되므로 새 host 를 도입하면
에이전트 재배포가 따른다.

## 한계

1. `task_type` 이 `zconverter_install` 1종이라 uninstall·rollback·재시작 등 다른 작업은 지원하지 않는다. 표시
   라벨 매핑(`mappers/task.py` `_TASK_TYPE_LABEL`)은 미지 값을 raw 그대로 폴백한다.
2. 중복 발행 차단은 부분 UNIQUE `uq_tasks_pending_per_server_type`로 같은 서버의 같은 task type pending을 1건만
   허용한다. 현재는 `zconverter_install`만 발행하므로 같은 설치 작업이 있는 서버가 섞이면 batch 전체가 취소된다(409).
   마감 경과분은 발행 직전 정리와 reaper 전역 정리 양쪽이 해소한다.
3. 매 publish 마다 ZDM 에 HEAD 1회(cache hit) 또는 GET full(cache miss)이 나간다. 같은 LAN 을 가정한 지연이라
   다른 네트워크면 `ZDM_META_TOTAL_TIMEOUT_SEC` 안에 끝나야 503 을 피한다.
4. ZDM 좌표를 모달에서 일괄 입력하므로 N대 호스트가 서로 다른 ZDM 서버를 가리키는 시나리오는 지원하지 않는다.
   발행 단위로 동일 ZDM IP/User 가 적용된다.
5. stdout/stderr 를 UTF-8 로 가정해 호스트 OS locale 에 따라 깨질 수 있다. 에이전트가 binary 로 받아 latin-1
   폴백을 적용한다.

## 관련 문서

- `docs/reference/contracts/agent-data.md` "J1. task.result" — 메시지 schema·필드 카탈로그
- `docs/reference/rabbitmq.md` — exchange·queue·routing key 토폴로지와 큐 정책
- `docs/reference/contracts/env.md` — `ZDM_*`·`INSTALL_*` 키 카탈로그
- 구현 위치(발행 서비스·라우터·핸들러·reaper)는 `docs/reference/web/services.md`·`routers.md`·`consumer.md`
