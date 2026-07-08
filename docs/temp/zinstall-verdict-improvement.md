# ZConverter install 판정 개선 — exit-code 추측에서 실제 설치 신호로

성격: 임시 구현 참고 메모(삭제 자유). 인프라 시험대에서 suse11 zinstall 실패를 조사하다 드러난
엔진 판정 로직의 구조적 한계와 개선 방향을 엔진 구현 담당이 바로 쓸 수 있게 정리한 것.
docs/temp README 는 코드 path 추상화를 권고하지만, 본 메모는 외부 공유가 아니라 내부 구현 지시가
목적이라 엔진 코드 경로/심볼을 명시한다.

## 한 줄 요약

install 성패를 installer 종료코드로만 판정하는 현재 로직은 false positive(설치 실패인데 success)와
진단 소실을 낸다. 방향은 실제 설치 신호(ZConverter 데몬 기동 + ZDM 등록)를 판정 1순위로 올리고,
실패는 구조화해 보존하는 것. allowlist 확장은 해법이 아니다.

## 실측 증거 (SysV 두 대, 같은 installer)

인프라 시험대 fleet 의 순수 SysV(systemd 없음) 두 대에 동일 ZConverter installer 로 zinstall 을
돌린 결과. 엔진 DB tasks 기록.

| 호스트 | os | glibc | installer exit | 엔진 저장 status | 실제 데몬 |
|---|---|---|---|---|---|
| centos6bios | centos 6.9 | 2.12 | 0 | success | 미기동 |
| suse11 | sles 11.2 | 2.11.3 | 1 (script_failed) | failure | 미기동 |

두 대 로그의 공통 사실:
- 둘 다 stderr 에 `/ZConverterAgent/ZConLinuxCloudAgent: /lib64/libc.so.6: version GLIBC_2.14
  not found`. installer 가 심는 ZConLinuxCloudAgent 바이너리가 glibc 2.14+ 링크라, 두 OS(2.11.3 /
  2.12) 모두 로드 불가. stdout 도 둘 다 `Checking zconverter process ... Failed`.
- 즉 데몬은 양쪽 다 실제로 안 떴다. 그런데 엔진 판정은 서로 갈렸다.

갈린 이유(installer 내부, 엔진 밖):
- installer 가 `/etc/os-release` 로 OS 를 판별하는데 SLES11 SP2 엔 그 파일이 없다(둘 다 stderr 에
  `grep: /etc/os-release: No such file`). centos6 는 그래도 `/etc/redhat-release` 로 el6 판별에
  성공해 맞는 lib 경로를 쓰고 installer 가 exit 0. suse11 은 판별에 전부 실패해 Debian/Ubuntu 분기로
  폴백 -> `libmysqlclient.so.18` 을 Debian 멀티아치 경로 `/usr/lib/x86_64-linux-gnu/` 에 넣으려다
  경로 부재로 cp 실패 -> installer exit 1.

결론: exit code 는 "설치가 실제로 됐나"와 상관이 약하다. centos6 의 exit 0 은 데몬 미기동을 못 걸러
success 로 신뢰됐고(false positive), suse11 의 exit 1 은 데몬과 무관한 앞단(lib 배치)에서 우연히
먼저 깨져 실패로 잡혔다. installer 결함(OS 판별, glibc 2.14 요구)은 ZConverter 제품 측이라 엔진이
못 고친다. 엔진이 통제할 수 있는 건 "판정 정확도"와 "진단 보존"뿐이다.

## 현재 엔진 구현 (개선 대상)

- 판정 단일 지점: `src/assessment_engine/task_policy.py::effective_task_result()`.
  입력 status/failure_reason/exit_code + os_family/os_id/os_version -> (effective_status,
  effective_failure_reason). exit_code 는 raw 보존, status/failure_reason 만 보정.
- 호출: `src/assessment_engine/consumer/handlers/task_result.py` (worker.result 소비 -> 보정 ->
  tasks UPDATE).
- allowlist: `src/assessment_engine/config.py` `task_install_success_exit_codes`
  = {"windows":[2],"rocky:9":[3],"almalinux:9":[3],"ol:9":[3],"centos:9":[3]}.
- 보정 조건: status=="failure" && failure_reason=="script_failed" && exit_code in allowlist[key].
  Linux 키 = f"{os_id}:{os_version.split('.')[0]}".
- 한계: 판정의 유일 입력이 exit_code. 실제 설치 신호(데몬/등록) 미확인. 스키마
  `TaskResultInput`(consumer/schemas.py:309)·tasks 테이블 어디에도 install_verified 류 필드 없음.

allowlist 는 "설치 성공인데 non-zero 로 끝나는 false-failure"(Windows exit 2, EL9 exit 3)만 골라
success 로 덮는 우회다. 이번 케이스는 성격이 반대(설치 실패)라 allowlist 로 풀 문제가 아니다.

## 최선의 방향

### 1순위 — 실제 설치 신호 기반 판정 (근본)

exit_code 추측을 대체하는 install_verified 신호. agent worker 가 installer 실행 직후 실제 설치
상태를 점검해 task.result 에 실어 보내고, 엔진이 이를 exit_code 보정보다 우선한다.

agent(worker) 측 (선행 필요):
- installer 종료 후 실제 신호 점검. Linux: ZConLinuxCloudAgent 데몬 실행 여부(프로세스/서비스) +
  ZDM 등록 여부. Windows: `ZConCloudAgent` 서비스 RUNNING.
- task.result 에 `install_verified: bool`(+가능하면 `verify_detail: str`) 추가 발행.

engine 측:
- `TaskResultInput`(consumer/schemas.py)에 `install_verified: bool | None = None` 추가.
- `effective_task_result()` 판정 우선순위 재정의:
  1. install_verified 가 False -> 무조건 failure (exit_code 0 이어도. centos6 false positive 차단).
  2. install_verified 가 True -> success (exit_code non-zero 여도. EL9/Windows false-failure 를
     allowlist 없이 정공으로 흡수).
  3. install_verified 가 None(구버전 agent) -> 현행 exit_code + allowlist 로 폴백(하위 호환).
- tasks 테이블에 install_verified 컬럼 추가(alembic). DB UPDATE 경로(TaskResultUpdate,
  complete_task) 에 필드 전달.
- allowlist 는 install_verified 미보고 agent 용 레거시 폴백으로 격하. 신규 OS 를 allowlist 로
  늘리지 않는다.

이 방향이면 이번 두 대 모두 정확해진다: install_verified=False -> centos6/suse11 둘 다 failure,
사유는 "데몬 미기동".

### 2순위 — 실패 진단 구조화 (보조, 엔진 단독 가능)

지금 실패는 failure_reason="script_failed" 한 값으로 뭉개지고 원인은 stderr_tail 텍스트에만 있다.
운영/집계에 쓰려면 분류가 필요하다.

- 후보 분류: os_unsupported(installer 가 OS 판별 실패 -> Debian 폴백 흔적), runtime_incompat
  (glibc 버전 부족), permission_denied, dependency_missing, generic_script_failed.
- 최소안: stderr_tail 을 엔진에서 후처리해 진단 태그를 파생 필드로 저장(예 GLIBC_.* not found ->
  runtime_incompat). agent 변경 없이 엔진 단독으로 착수 가능. 단 문자열 매칭이라 취약 —
  1순위(install_verified)가 자리잡으면 이건 부가 진단으로만.

## 하지 말 것

- suse11(sles:11)을 allowlist 에 추가하지 말 것. 실제 설치 실패라 success 로 덮으면 false positive
  만 는다. allowlist 는 "성공인데 non-zero"에만 쓰는 도구다.
- centos6 의 exit 0 success 를 정상으로 신뢰하지 말 것. 데몬 미기동이라 사실상 false positive.
  install_verified 도입 시 재판정 대상.

## 의존성 / 순서

- 1순위 완성은 agent worker 변경(install_verified 발행)이 선행돼야 한다. 엔진 단독으로는 exit_code
  라는 신뢰 하한을 못 넘는다.
- 엔진이 지금 당장 할 수 있는 것: (a) install_verified 를 optional 로 받는 스키마/판정/DB 준비(agent
  가 아직 안 보내도 None 폴백으로 무해), (b) 2순위 진단 태그 파생. (a)를 먼저 넣어두면 agent 가
  발행을 시작하는 즉시 정공 판정이 켜진다.

## 엔진 측 진행 상태

(a) install_verified optional 스캐폴드 — 구현·검증 완료 (feature/new, 커밋 전):
- `task_policy.effective_task_result` 판정 우선순위 재정의 (True->success / False->failure(exit0 이어도,
  사유 없으면 install_unverified) / None->레거시 exit_code+allowlist 폴백). 단위 테스트 4 케이스.
- `TaskResultInput`(consumer/schemas)·`TaskResultUpdate`(inbound DTO)·handler·`tasks.install_verified`
  컬럼(alembic b5e8f1a3c7d9)·`complete_task` UPDATE·failure_reason 라벨(install_unverified). raw 보존.
- 검증: 단위 642 + 통합 129 통과, install_verified 실값 영속 확인, 마이그레이션 라운드트립.
- 남은 선행: agent worker 의 install_verified 발행 (엔진 밖). 발행 시작 즉시 정공 판정 자동 활성.

(b) 진단 태그 파생 — 미착수 (메모대로 1순위 자리잡은 뒤 부가 진단으로).
display read-path(task 상세에 install_verified 노출) — 미착수 (failure_reason 라벨로 결과는 이미 표시).
