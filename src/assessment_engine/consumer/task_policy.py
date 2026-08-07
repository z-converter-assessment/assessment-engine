"""task.result 성공/실패 판정 정책.

`consumer/` 에 있는 이유는 소비자가 `handlers/task_result.py` 하나이고, 정책 입력인
`task_install_success_exit_codes` 도 `ConsumerSettings` 소유이기 때문이다. `domain/` 의 도메인
모듈(`right_sizing`·`service_classifier`)은 web·consumer 양쪽이 쓰는 것들이고, 이건 아니다.

판정 1순위는 실제 설치 신호 task_policy(agent worker 가 데몬 기동+등록을 확인해 발행하는 bool) —
exit_code 는 "설치가 실제로 됐나"와 상관이 약해(exit 0 인데 데몬 미기동 = false positive) 보조로 내린다.

"어떤 exit code 를 성공으로 볼지"의 폴백 정책은 컴파일된 에이전트 바이너리가 아니라 엔진이 소유한다 —
재배포 없이 allowlist 확장으로 대응한다. 키 규약·기본값·그 근거(설치는 성공인데 installer 가 non-zero 로
끝나는 케이스)는 `docs/reference/contracts/env.md` 의 `TASK_INSTALL_SUCCESS_EXIT_CODES` 단일 진실.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

# 보정 대상을 이 사유로 한정한다 — download/extract/timeout 등 실행 전·중단 실패는 exit code 가 의미 없다.
_SCRIPT_FAILED_REASON = "script_failed"
# task_policy=False 인데 raw 에는 실패 사유가 없는(exit 0 success 보고) 경우의 override 사유.
_UNVERIFIED_REASON = "install_unverified"


def effective_task_result(
    status: str,
    failure_reason: str | None,
    exit_code: int | None,
    os_family: str | None,
    os_version: str | None,
    os_id: str | None,
    success_exit_codes: Mapping[str, Sequence[int]],
    task_policy: bool | None = None,
) -> tuple[str, str | None]:
    """raw 보고 결과 -> 정책 적용 후 (effective_status, effective_failure_reason).

    판정 우선순위 — 실제 설치 신호(task_policy)가 exit_code 추측보다 우선:
      1. task_policy True  -> success. non-zero exit(false-failure)도 allowlist 없이 흡수.
      2. task_policy False -> failure. exit 0 이어도 데몬 미기동이면 실패.
      3. task_policy None(미보고) -> exit_code + allowlist 폴백. status=failure + script_failed +
         매칭 키의 허용 코드일 때만 success 로 전환하고, 그 외에는 입력을 그대로 통과시킨다.

    allowlist 는 그 폴백 전용이다 — 신규 OS 를 allowlist 로 늘리지 않는다.
    """
    if task_policy is True:
        return "success", None
    if task_policy is False:
        return "failure", failure_reason or _UNVERIFIED_REASON
    if status != "failure" or failure_reason != _SCRIPT_FAILED_REASON or exit_code is None:
        return status, failure_reason
    keys: list[str] = []
    if os_family == "windows":
        if os_version:
            keys.append(os_version)
        keys.append("windows")
    elif os_family == "linux" and os_id and os_version:
        # linux 는 family 키를 두지 않는다 — 보정 대상이 EL9 계열 특정이라 배포판 전체로 넓히면 오탐이 된다.
        keys.append(f"{os_id}:{os_version.split('.')[0]}")
    for key in keys:
        allowed = success_exit_codes.get(key)
        if allowed is not None and exit_code in allowed:
            return "success", None
    return status, failure_reason
