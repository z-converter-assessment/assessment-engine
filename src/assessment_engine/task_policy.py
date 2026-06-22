"""task.result 성공/실패 판정 정책 — OS 버전별 성공 exit code 보정 (도메인 모듈).

에이전트(worker)는 exit_code 와 OS 식별자(os_family·os_version)를 raw 로 보고하고,
"어떤 exit code 를 성공으로 볼지"의 정책은 엔진이 소유한다 (CLAUDE.md #B "필요해진 시점에
명시적 결정", 에이전트 raw-fact 철학과 정합). 정책이 컴파일된 에이전트 바이너리 밖 엔진에
있어 재배포 없이 allowlist(ConsumerSettings.task_install_success_exit_codes) 확장으로 대응.

배경: 일부 Windows 버전(예: Windows Server 2022, CurrentBuildNumber=20348)에서 ZConverter
installer 가 설치 성공임에도 exit code 2 로 종료해 에이전트가 status=failure(script_failed)
로 보고한다. 본 정책은 그 케이스만 골라 effective status 를 success 로 보정한다.
"""

from collections.abc import Mapping, Sequence

# 보정 대상은 "스크립트가 실제로 실행돼 non-zero 로 끝난" 경우(script_failed)로 한정.
# download/extract/timeout/internal 등 실행 전·중단 실패는 exit code 의미가 없어 보정 제외.
_SCRIPT_FAILED_REASON = "script_failed"


def effective_task_result(
    status: str,
    failure_reason: str | None,
    exit_code: int | None,
    os_family: str | None,
    os_version: str | None,
    success_exit_codes: Mapping[str, Sequence[int]],
) -> tuple[str, str | None]:
    """raw 보고 결과 -> 정책 적용 후 (effective_status, effective_failure_reason).

    보정 조건(전부 충족 시에만 success 로 전환):
      - status == "failure" 이고 failure_reason == "script_failed"
      - os_family == "windows" 이고 os_version 이 allowlist 키와 일치
      - exit_code 가 해당 os_version 의 허용 코드 목록에 포함

    그 외 모든 경우는 입력을 그대로 통과 (보정 없음).
    """
    if status != "failure" or failure_reason != _SCRIPT_FAILED_REASON:
        return status, failure_reason
    if os_family != "windows" or not os_version or exit_code is None:
        return status, failure_reason
    allowed = success_exit_codes.get(os_version)
    if allowed is not None and exit_code in allowed:
        return "success", None
    return status, failure_reason
