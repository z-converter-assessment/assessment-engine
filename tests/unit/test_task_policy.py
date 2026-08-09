from assessment_engine.consumer.task_policy import effective_task_result

ALLOW = {"20348": [2], "rocky:9": [3], "almalinux:9": [3], "ol:9": [3], "centos:9": [3]}


def _eff(
    status: str,
    reason: str | None,
    exit_code: int | None,
    os_family: str | None,
    os_version: str | None,
    os_id: str | None = None,
) -> tuple[str, str | None]:
    return effective_task_result(
        status=status,
        failure_reason=reason,
        exit_code=exit_code,
        os_family=os_family,
        os_version=os_version,
        os_id=os_id,
        success_exit_codes=ALLOW,
    )


def test_win2022_exit2_remapped_to_success() -> None:
    assert _eff("failure", "script_failed", 2, "windows", "20348") == ("success", None)


def test_other_build_not_remapped() -> None:
    assert _eff("failure", "script_failed", 2, "windows", "17763") == ("failure", "script_failed")


def test_other_exit_code_not_remapped() -> None:
    assert _eff("failure", "script_failed", 1, "windows", "20348") == ("failure", "script_failed")


def test_null_os_version_not_remapped() -> None:
    assert _eff("failure", "script_failed", 2, "windows", None) == ("failure", "script_failed")


def test_el9_rocky_exit3_remapped_to_success() -> None:
    assert _eff("failure", "script_failed", 3, "linux", "9.7", "rocky") == ("success", None)


def test_el9_centos9_exit3_remapped() -> None:
    assert _eff("failure", "script_failed", 3, "linux", "9", "centos") == ("success", None)


def test_rhel9_exit3_not_remapped() -> None:
    assert _eff("failure", "script_failed", 3, "linux", "9.4", "rhel") == ("failure", "script_failed")


def test_rocky8_exit3_not_remapped() -> None:
    assert _eff("failure", "script_failed", 3, "linux", "8.10", "rocky") == ("failure", "script_failed")


def test_linux_other_exit_code_not_remapped() -> None:
    assert _eff("failure", "script_failed", 1, "linux", "9.7", "rocky") == ("failure", "script_failed")


def test_linux_without_os_id_not_remapped() -> None:
    assert _eff("failure", "script_failed", 3, "linux", "9.7", None) == ("failure", "script_failed")


def test_non_script_failed_not_remapped() -> None:
    assert _eff("failure", "script_timeout", 2, "windows", "20348") == ("failure", "script_timeout")
    assert _eff("failure", "script_timeout", 3, "linux", "9.7", "rocky") == ("failure", "script_timeout")


def test_null_exit_code_not_remapped() -> None:
    assert _eff("failure", "script_failed", None, "windows", "20348") == ("failure", "script_failed")


def test_success_passthrough() -> None:
    assert _eff("success", None, 0, "windows", "20348") == ("success", None)


def _effv(
    status: str,
    reason: str | None,
    exit_code: int | None,
    task_policy: bool | None,
    os_family: str | None = "linux",
    os_version: str | None = None,
    os_id: str | None = None,
) -> tuple[str, str | None]:
    return effective_task_result(
        status=status,
        failure_reason=reason,
        exit_code=exit_code,
        os_family=os_family,
        os_version=os_version,
        os_id=os_id,
        success_exit_codes=ALLOW,
        task_policy=task_policy,
    )


def test_verified_true_absorbs_nonzero_failure_without_allowlist() -> None:
    assert _effv("failure", "script_failed", 3, True, os_id="sles", os_version="15") == ("success", None)


def test_verified_false_overrides_exit0_success_to_failure() -> None:
    assert _effv("success", None, 0, False, os_id="centos", os_version="6") == ("failure", "install_unverified")


def test_verified_false_preserves_reported_failure_reason() -> None:
    assert _effv("failure", "script_failed", 1, False, os_id="sles", os_version="11") == ("failure", "script_failed")


def test_verified_none_falls_back_to_allowlist() -> None:
    assert _effv("failure", "script_failed", 3, None, os_id="rocky", os_version="9") == ("success", None)
