"""task_policy.effective_task_result 단위 테스트.

성공 exit code 보정 정책 — Windows Server 2022(build 20348) + exit code 2 + script_failed
일 때만 status 를 success 로 보정. 그 외 모든 경우는 입력 그대로 통과.
allowlist 변경 시 본 test 회귀 가시화.
"""

from assessment_engine.task_policy import effective_task_result

ALLOW = {"20348": [2]}


def _eff(status, reason, exit_code, os_family, os_version):
    return effective_task_result(
        status=status,
        failure_reason=reason,
        exit_code=exit_code,
        os_family=os_family,
        os_version=os_version,
        success_exit_codes=ALLOW,
    )


def test_win2022_exit2_remapped_to_success() -> None:
    assert _eff("failure", "script_failed", 2, "windows", "20348") == ("success", None)


def test_other_build_not_remapped() -> None:
    """2019(17763) 등 allowlist 밖 빌드는 exit 2 여도 보정 안 함."""
    assert _eff("failure", "script_failed", 2, "windows", "17763") == ("failure", "script_failed")


def test_other_exit_code_not_remapped() -> None:
    """2022 라도 allowlist 밖 exit code(1)는 보정 안 함."""
    assert _eff("failure", "script_failed", 1, "windows", "20348") == ("failure", "script_failed")


def test_non_script_failed_not_remapped() -> None:
    """script 가 실제 실행돼 종료한 경우(script_failed)만 대상 — timeout 등은 제외."""
    assert _eff("failure", "script_timeout", 2, "windows", "20348") == ("failure", "script_timeout")


def test_non_windows_not_remapped() -> None:
    assert _eff("failure", "script_failed", 2, "linux", "20348") == ("failure", "script_failed")


def test_null_os_version_not_remapped() -> None:
    assert _eff("failure", "script_failed", 2, "windows", None) == ("failure", "script_failed")


def test_null_exit_code_not_remapped() -> None:
    assert _eff("failure", "script_failed", None, "windows", "20348") == ("failure", "script_failed")


def test_success_passthrough() -> None:
    assert _eff("success", None, 0, "windows", "20348") == ("success", None)
