"""task.result의 최종 성공 또는 실패 상태를 결정하는 Consumer 정책."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

# 보정 대상을 이 사유로 한정한다 — download/extract/timeout 등 실행 전·중단 실패는 exit code 가 의미 없다.
_SCRIPT_FAILED_REASON = "script_failed"

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
    """agent 정책 신호와 OS별 exit code allowlist로 task 결과를 보정한다.

    task_policy가 있으면 이를 우선하고, 없을 때만 script_failed 결과를 allowlist로 보정한다.
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
