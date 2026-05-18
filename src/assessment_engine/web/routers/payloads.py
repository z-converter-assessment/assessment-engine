"""Install payload router — Linux/Windows OS별 ZConverter agent 설치 스크립트 번들 제공.

본 endpoint는 본 엔진이 self-host하는 install 번들. task.install 메시지의 download.url은
본 endpoint를 가리키도록 발행되고, 원격 호스트는 그 URL을 그대로 fetch한 뒤 sha256·size_bytes를
페이로드 값과 검증한다 (#F8 폐쇄망 가정).

bundle 안에 install.sh (Linux) + install.ps1 (Windows) 두 스크립트 포함. 어느 스크립트를 실행할지는
task.install 페이로드의 install.script 필드로 OS별 분기 (web/services/task_service.py).
두 스크립트 모두 `-s ZDM_IP -u ZDM_USER` 인자 받아 ZDM 서버에서 실제 setup 패키지 fetch + 실행.
"""
import hashlib
import io
import tarfile

from fastapi import APIRouter
from fastapi.responses import Response

from assessment_engine.web.settings import web_settings

payloads_router = APIRouter(tags=["payloads"])

# APP_ENV=dev 환경 한정 mock 스크립트 — ZDM 서버 의존 skip, bundle 받으면 즉시 success exit.
# 실제 ZDM 서버(192.168.3.94 등) 없이도 install task 완결 경로 검증 가능.
# prod 에서는 본 mock 미사용 — 아래 _INSTALL_SCRIPT_{LINUX,WINDOWS} 실제 wrapper 사용.

_INSTALL_SCRIPT_LINUX = b"""#!/bin/sh
# ZConverter Agent Installation Script for Linux
set -eu

ZDM_IP=""
ZDM_USER=""
while [ $# -gt 0 ]; do
  case "$1" in
    -s) ZDM_IP="${2:-}"; shift 2 ;;
    -u) ZDM_USER="${2:-}"; shift 2 ;;
    *)  shift ;;
  esac
done

if [ -z "$ZDM_IP" ] || [ -z "$ZDM_USER" ]; then
  echo "[install.sh] missing -s ZDM_IP or -u ZDM_USER" >&2
  exit 2
fi

# Strip scheme + path if user supplied a full URL (e.g. http://host:port/path -> host:port).
ZDM_HOST=$(echo "$ZDM_IP" | sed -E 's|^https?://||; s|/.*||')

curl -fsSL -o /tmp/ZConverter_CloudSource_Setup_Linux.tar.gz \\
  "http://$ZDM_HOST/download/ZConverter_CloudSource_Setup_Linux.tar.gz"
tar zxvf /tmp/ZConverter_CloudSource_Setup_Linux.tar.gz -C /tmp
/tmp/zconverter_install_source/install.sh -s "$ZDM_HOST" -u "$ZDM_USER"
"""

_INSTALL_SCRIPT_WINDOWS = b"""# ZConverter Agent Installation Script for Windows
param(
    [string]$s = "",
    [string]$u = ""
)

$ZDM_IP   = $s
$ZDM_USER = $u

if ([string]::IsNullOrEmpty($ZDM_IP) -or [string]::IsNullOrEmpty($ZDM_USER)) {
    Write-Error "[install.ps1] missing -s ZDM_IP or -u ZDM_USER"
    exit 2
}

$TempDir = "$env:TEMP\\ZConverter"
$ExeFile = "$TempDir\\ZConverter_CloudSource_Setup_Windows.exe"
$Url     = "http://$ZDM_IP/download/ZConverter_CloudSource_Setup_Windows.exe"

if (!(Test-Path $TempDir)) {
    New-Item -ItemType Directory -Path $TempDir | Out-Null
}

curl.exe -L -o "$ExeFile" "$Url"
Start-Process $ExeFile "-s $ZDM_IP -u $ZDM_USER" -Wait
"""

# tar 내부 파일명. task.install 페이로드의 install.script 필드 값과 일치.
INSTALL_SCRIPT_LINUX = "install.sh"
INSTALL_SCRIPT_WINDOWS = "install.ps1"

_LINUX_MODE = 0o755
_WINDOWS_MODE = 0o644


def _build_install_bundle() -> bytes:
    """in-memory tar.gz 생성. mtime epoch 고정 + USTAR 포맷으로 같은 코드면 같은 bytes (sha256 안정).

    tarfile.TarInfo에 명시:
    - mode: Linux sh는 실행 권한 (0o755), Windows ps1은 일반 파일 (0o644 — powershell.exe -File 로 실행).
    - uid/gid: 0 (root) — tar 안 메타.
    - mtime: 0 (epoch) — deterministic.
    - format=USTAR_FORMAT — Python 기본 PAX 는 atime/ctime 도 헤더에 저장해 시각마다 다른 bytes 생성.
      USTAR 는 mtime 만 저장 → 시각 무관 deterministic.
    - gzip mtime=0 — gzip 헤더의 mtime 도 0 고정 (compresslevel=6 + mtime=0 으로 같은 결과 보장).
    """
    buf = io.BytesIO()
    # gzip mtime=0 명시 — tarfile 의 mode="w:gz" 가 GzipFile 내부적으로 사용. mtime 인자 직접 제어 위해 wrap.
    import gzip
    gz = gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=6, mtime=0)
    with tarfile.open(fileobj=gz, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        for name, body, mode in (
            (INSTALL_SCRIPT_LINUX, _INSTALL_SCRIPT_LINUX, _LINUX_MODE),
            (INSTALL_SCRIPT_WINDOWS, _INSTALL_SCRIPT_WINDOWS, _WINDOWS_MODE),
        ):
            info = tarfile.TarInfo(name=name)
            info.size = len(body)
            info.mode = mode
            info.uid = 0
            info.gid = 0
            info.uname = "root"
            info.gname = "root"
            info.mtime = 0
            tar.addfile(info, io.BytesIO(body))
    gz.close()
    return buf.getvalue()


_BUNDLE_CACHE: bytes = _build_install_bundle()
INSTALL_BUNDLE_SIZE: int = len(_BUNDLE_CACHE)
INSTALL_BUNDLE_SHA256: str = hashlib.sha256(_BUNDLE_CACHE).hexdigest()


# dev 한정 ZDM mock setup tarball — install.sh 안 curl 의 ZDM download 단계를 engine 이 응답.
# tar 안 zconverter_install_source/install.sh 가 exit 0 (실제 install 시뮬레이션). ZDM 서버 없이 dev 검증 가능.
_ZDM_MOCK_INNER_INSTALL_SH = b"""#!/bin/sh
# DEV MOCK inner install.sh - dev verification path, exit 0 without actual install.
set -eu
ZDM_IP=""
ZDM_USER=""
while [ $# -gt 0 ]; do
  case "$1" in
    -s) ZDM_IP="${2:-}"; shift 2 ;;
    -u) ZDM_USER="${2:-}"; shift 2 ;;
    *)  shift ;;
  esac
done
echo "[zconverter_install_source/install.sh dev-mock] ZDM_IP=$ZDM_IP ZDM_USER=$ZDM_USER"
echo "skipping actual install (dev verification path)"
exit 0
"""


def _build_zdm_mock_linux_tarball() -> bytes:
    """dev mock — `/download/ZConverter_CloudSource_Setup_Linux.tar.gz` 응답 본체.

    tar 안 path: `zconverter_install_source/install.sh` (실제 ZDM 패키지 구조와 동일).
    내용: exit 0 mock — install.sh wrapper 의 마지막 단계가 정상 success.
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", compresslevel=6) as tar:
        info = tarfile.TarInfo(name="zconverter_install_source/install.sh")
        info.size = len(_ZDM_MOCK_INNER_INSTALL_SH)
        info.mode = 0o755
        info.uid = 0
        info.gid = 0
        info.uname = "root"
        info.gname = "root"
        info.mtime = 0
        tar.addfile(info, io.BytesIO(_ZDM_MOCK_INNER_INSTALL_SH))
    return buf.getvalue()


_ZDM_MOCK_LINUX_TARBALL: bytes = _build_zdm_mock_linux_tarball()


@payloads_router.get("/zconverter.tar.gz")
async def get_install_bundle() -> Response:
    """원격 호스트가 task.install download.url로 fetch.

    인증 없음 — 폐쇄망 가정 (#F8). 외부 노출 시 별도 ADR로 인증·rate limit 도입.
    """
    return Response(
        content=_BUNDLE_CACHE,
        media_type="application/gzip",
        headers={
            "Content-Disposition": 'attachment; filename="zconverter.tar.gz"',
            "Cache-Control": "no-cache",
        },
    )


@payloads_router.get("/download/ZConverter_CloudSource_Setup_Linux.tar.gz")
async def get_zdm_mock_linux() -> Response:
    """dev 한정 ZDM 흉내 endpoint — install.sh wrapper 의 `curl http://$ZDM_IP/download/...` 단계 응답.

    운영자가 모달 ZDM IP 칸에 engine host (예: `host.lima.internal:8000`) 박으면 본 endpoint 가
    `/download/ZConverter_CloudSource_Setup_Linux.tar.gz` path 로 hit. dev 검증 경로.
    prod 에서는 본 endpoint 무관 (운영자가 실제 ZDM IP 박으니 호출 안 됨).
    """
    if web_settings.app_env != "dev":
        return Response(status_code=404, content="not found")
    return Response(
        content=_ZDM_MOCK_LINUX_TARBALL,
        media_type="application/gzip",
        headers={
            "Content-Disposition": 'attachment; filename="ZConverter_CloudSource_Setup_Linux.tar.gz"',
            "Cache-Control": "no-cache",
        },
    )
