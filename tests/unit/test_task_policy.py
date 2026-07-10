"""task_policy.effective_task_result 단위 테스트.

성공 exit code 보정 정책:
- Windows: build(os_version)=20348 + exit 2 + script_failed → success.
- Linux: "os_id:major"(예 rocky:9) + exit 3 + script_failed → success (EL9 systemd start-limit false-failure).
그 외 모든 경우는 입력 그대로 통과. allowlist 변경 시 본 test 회귀 가시화.
"""

from assessment_engine.task_policy import effective_task_result

ALLOW = {"20348": [2], "rocky:9": [3], "almalinux:9": [3], "ol:9": [3], "centos:9": [3]}


def _eff(status, reason, exit_code, os_family, os_version, os_id=None):
    return effective_task_result(
        status=status,
        failure_reason=reason,
        exit_code=exit_code,
        os_family=os_family,
        os_version=os_version,
        os_id=os_id,
        success_exit_codes=ALLOW,
    )


# ─── Windows (build os_version 매칭) ──────────────────────────────────────────


def test_win2022_exit2_remapped_to_success() -> None:
    assert _eff("failure", "script_failed", 2, "windows", "20348") == ("success", None)


def test_other_build_not_remapped() -> None:
    """2019(17763) 등 allowlist 밖 빌드는 exit 2 여도 보정 안 함."""
    assert _eff("failure", "script_failed", 2, "windows", "17763") == ("failure", "script_failed")


def test_other_exit_code_not_remapped() -> None:
    """2022 라도 allowlist 밖 exit code(1)는 보정 안 함."""
    assert _eff("failure", "script_failed", 1, "windows", "20348") == ("failure", "script_failed")


def test_null_os_version_not_remapped() -> None:
    assert _eff("failure", "script_failed", 2, "windows", None) == ("failure", "script_failed")


# ─── Linux (os_id:major 매칭, EL9 exit 3) ─────────────────────────────────────


def test_el9_rocky_exit3_remapped_to_success() -> None:
    """rocky 9.7 + exit 3 + script_failed → success (마이너는 major 로 절단)."""
    assert _eff("failure", "script_failed", 3, "linux", "9.7", "rocky") == ("success", None)


def test_el9_centos9_exit3_remapped() -> None:
    """centos-stream 9 (os_id=centos, version=9) → success."""
    assert _eff("failure", "script_failed", 3, "linux", "9", "centos") == ("success", None)


def test_rhel9_exit3_not_remapped() -> None:
    """rhel 9 는 allowlist 밖(EL9 미해당) — exit 3 이어도 보정 안 함."""
    assert _eff("failure", "script_failed", 3, "linux", "9.4", "rhel") == ("failure", "script_failed")


def test_rocky8_exit3_not_remapped() -> None:
    """rocky 8 (major 8)은 allowlist 밖 — rocky:9 만 대상."""
    assert _eff("failure", "script_failed", 3, "linux", "8.10", "rocky") == ("failure", "script_failed")


def test_linux_other_exit_code_not_remapped() -> None:
    """rocky 9 라도 allowlist 밖 exit code(1)는 보정 안 함."""
    assert _eff("failure", "script_failed", 1, "linux", "9.7", "rocky") == ("failure", "script_failed")


def test_linux_without_os_id_not_remapped() -> None:
    """os_id 없으면 Linux 키 구성 불가 — 보정 안 함."""
    assert _eff("failure", "script_failed", 3, "linux", "9.7", None) == ("failure", "script_failed")


# ─── 공통 게이트 ──────────────────────────────────────────────────────────────


def test_non_script_failed_not_remapped() -> None:
    """script 가 실제 실행돼 종료한 경우(script_failed)만 대상 — timeout 등은 제외."""
    assert _eff("failure", "script_timeout", 2, "windows", "20348") == ("failure", "script_timeout")
    assert _eff("failure", "script_timeout", 3, "linux", "9.7", "rocky") == ("failure", "script_timeout")


def test_null_exit_code_not_remapped() -> None:
    assert _eff("failure", "script_failed", None, "windows", "20348") == ("failure", "script_failed")


def test_success_passthrough() -> None:
    assert _eff("success", None, 0, "windows", "20348") == ("success", None)


# ─── task_policy 우선순위 (실제 설치 신호 > exit_code, zinstall-verdict 개선) ──────────


def _effv(status, reason, exit_code, task_policy, os_family="linux", os_version=None, os_id=None):
    return effective_task_result(
        status=status, failure_reason=reason, exit_code=exit_code, os_family=os_family,
        os_version=os_version, os_id=os_id, success_exit_codes=ALLOW, task_policy=task_policy,
    )


def test_verified_true_absorbs_nonzero_failure_without_allowlist() -> None:
    # 데몬 기동+등록 확인 -> non-zero exit(allowlist 밖 OS 여도) 정공 success (EL9/Windows false-failure).
    assert _effv("failure", "script_failed", 3, True, os_id="sles", os_version="15") == ("success", None)


def test_verified_false_overrides_exit0_success_to_failure() -> None:
    # centos6 류 false positive — exit 0 success 보고여도 데몬 미기동이면 failure(install_unverified).
    assert _effv("success", None, 0, False, os_id="centos", os_version="6") == ("failure", "install_unverified")


def test_verified_false_preserves_reported_failure_reason() -> None:
    # 실패 보고 + 미확인 -> failure 유지, 원래 사유 보존(진단 소실 방지).
    assert _effv("failure", "script_failed", 1, False, os_id="sles", os_version="11") == ("failure", "script_failed")


def test_verified_none_falls_back_to_allowlist() -> None:
    # 구버전 agent(미발행) -> 레거시 exit_code + allowlist 폴백. rocky:9 exit 3 보정 유지.
    assert _effv("failure", "script_failed", 3, None, os_id="rocky", os_version="9") == ("success", None)
