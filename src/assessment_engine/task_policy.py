"""task.result 성공/실패 판정 정책 (도메인 모듈).

판정 1순위는 실제 설치 신호 install_verified(agent 가 데몬 기동+ZDM 등록을 확인해 발행) —
exit_code 는 "설치가 실제로 됐나"와 상관이 약해(exit 0 인데 데몬 미기동 = false positive) 보조로 내린다.
install_verified 미보고(구버전 agent)면 레거시 exit_code + allowlist 폴백으로 하위 호환.

에이전트(worker)는 exit_code 를 raw 로 보고하고, "어떤 exit code 를 성공으로 볼지"의 폴백 정책은
엔진이 소유한다 (CLAUDE.md #B "필요해진 시점에 명시적 결정", 에이전트 raw-fact 철학과 정합).
정책이 컴파일된 에이전트 바이너리 밖 엔진에 있어 재배포 없이 allowlist
(ConsumerSettings.task_install_success_exit_codes) 확장으로 대응 (단 allowlist 는 레거시 폴백 전용).

매칭 키 (os_family 분기, 구체 -> 일반 순):
- Windows: os_version(CurrentBuildNumber, 예 "20348") 우선 -> 없으면 family-level "windows".
           installer exit 2 = 성공이 전 세대 공통이라 family 키 하나로 일괄(빌드별 키 유지 회피, 빈 os_version 도 커버).
- Linux:   "os_id:major" (예 "rocky:9") — EL9 특정이라 family 키 없음. task.result 가 os 미발행이라
           handler 가 inventory 에서 대상 서버 os_id/os_version 을 조회해 전달 (에이전트 재배포 불요).

배경 (둘 다 설치·등록 성공인데 installer 가 non-zero 로 끝나는 false-failure):
- Windows Server 2022(build 20348): installer exit code 2.
- EL9 계열(rocky/almalinux/ol/centos major 9): ZConverter installer 의 systemd 유닛이 새 systemd
  의 start-limit 에 걸려 exit 3 (설치·ZDM 등록은 성공). 근본 해결은 installer 유닛 수정, 본 정책은 우회.
본 정책은 그 케이스만 골라 effective status 를 success 로 보정한다.
"""

from collections.abc import Mapping, Sequence

# 보정 대상은 "스크립트가 실제로 실행돼 non-zero 로 끝난" 경우(script_failed)로 한정.
# download/extract/timeout/internal 등 실행 전·중단 실패는 exit code 의미가 없어 보정 제외.
_SCRIPT_FAILED_REASON = "script_failed"
# install_verified=False 인데 raw 로는 실패 사유가 없는(예: exit 0 success 보고) 경우의 override 사유.
# 실제 설치 신호(데몬 기동/등록) 미확인 -> false positive 차단. 원래 실패 사유가 있으면 그걸 보존.
_UNVERIFIED_REASON = "install_unverified"


def effective_task_result(
    status: str,
    failure_reason: str | None,
    exit_code: int | None,
    os_family: str | None,
    os_version: str | None,
    os_id: str | None,
    success_exit_codes: Mapping[str, Sequence[int]],
    install_verified: bool | None = None,
) -> tuple[str, str | None]:
    """raw 보고 결과 -> 정책 적용 후 (effective_status, effective_failure_reason).

    판정 우선순위 — 실제 설치 신호(install_verified)가 exit_code 추측보다 우선:
      1. install_verified True  -> success. agent 가 데몬 기동+ZDM 등록을 확인 -> non-zero exit(EL9 exit 3·
         Windows exit 2 같은 false-failure)도 allowlist 없이 정공 흡수.
      2. install_verified False -> failure. exit 0 이어도 데몬 미기동이면 실패 (centos6 류 false positive 차단).
         원래 실패 사유가 있으면 보존, 없으면(success 보고였음) _UNVERIFIED_REASON.
      3. install_verified None(구버전 agent, 미발행) -> 아래 레거시 exit_code + allowlist 폴백(하위 호환).

    레거시 폴백 보정 조건(전부 충족 시에만 success 로 전환):
      - status == "failure" 이고 failure_reason == "script_failed"
      - exit_code 가 매칭 키의 허용 코드 목록에 포함
      - 매칭 키 후보 (구체 -> 일반 순, os_family 분기):
        * Windows: os_version(CurrentBuildNumber, 예 "20348") 우선 -> 없으면 family-level "windows".
                   installer exit 2 = 성공이 전 윈도우 세대 공통이라 family 키 하나로 일괄(빌드별 유지 회피).
        * Linux:   "os_id:major" (예 "rocky:9") — major = os_version 의 첫 토큰. EL9 특정이라 family 키 없음.
                   os_id/os_version 은 agent 가 task.result 에 inventory 와 동일 소스로 발행 (handler 가 그대로 전달).

    allowlist 는 install_verified 미보고 agent 전용 레거시 폴백이다 — 신규 OS 를 allowlist 로 늘리지 않는다.
    그 외 모든 경우는 입력을 그대로 통과 (보정 없음).
    """
    if install_verified is True:
        return "success", None  # 실제 설치 확인 -> non-zero exit false-failure 정공 흡수
    if install_verified is False:
        return "failure", failure_reason or _UNVERIFIED_REASON  # 데몬 미기동 -> false positive 차단
    if status != "failure" or failure_reason != _SCRIPT_FAILED_REASON or exit_code is None:
        return status, failure_reason
    keys: list[str] = []
    if os_family == "windows":
        if os_version:
            keys.append(os_version)
        keys.append("windows")
    elif os_family == "linux" and os_id and os_version:
        keys.append(f"{os_id}:{os_version.split('.')[0]}")
    for key in keys:
        allowed = success_exit_codes.get(key)
        if allowed is not None and exit_code in allowed:
            return "success", None
    return status, failure_reason
