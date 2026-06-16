"""dev 한정 ZDM mock endpoint — ADR 0018.

운영자가 list 모달에서 install 발행 시 agent worker 가 fetch 하는 ZDM 본체 패키지를
dev 한정으로 web 컨테이너가 직접 serve. ADR 0016 의 단일 단계 흐름 (agent → ZDM 직접
fetch) 사상 유지 — engine 은 "ZDM 측 역할" 을 dev 한정으로 재현할 뿐, prod 에서는
라우터 자체가 등록 안 됨 (web/main.py 분기).

설계:
- 더미 tar.gz — startup 1회 in-memory build + bytes 캐싱 (요청 시 재계산 없음, sha256 안정)
- tar 내용 = `zconverter_install_source/install.sh` (운영 ZDM 본체 패키지 layout 과 동일 경로) —
  인자 `-s ZDM_IP -u ZDM_USER` 받아 stdout 에 echo + exit 0. libvirt VM 에서 success 경로 실증용.
- 응답 헤더 (HEAD/GET 동일):
    Content-Length   HttpZdmPackageResolver HEAD 의 size 추출
    ETag             Redis cache 키 (불변 tarball 이라 sha256 hex 그대로)
    Last-Modified    ETag 없을 때 fallback (정합성 위한 명시, resolver 는 ETag 우선)
- HEAD 는 별도 dispatch 안 됨 (FastAPI 가 GET → HEAD 자동 매핑 안 함) →
  `api_route(methods=["GET", "HEAD"])` 로 두 메서드 모두 받고 핸들러 안에서 분기.
"""

import gzip
import hashlib
import io
import pathlib
import tarfile
from email.utils import formatdate

from fastapi import APIRouter, Request
from fastapi.responses import Response
from loguru import logger

from assessment_engine.web.settings import web_settings

dev_zdm_mock_router = APIRouter(tags=["dev-zdm-mock"])

# 더미 install.sh — task_service._publish_install 이 박는 ["-s", ZDM_IP, "-u", ZDM_USER] 그대로 수신.
# 실제 ZConverter install 로직을 흉내 내지 않고 인자 확인 + exit 0 만 — task.result 가 success 로
# 돌아오는지 (consumer 가 6 컬럼 UPDATE 하는지) E2E 평가가 목적.
_INSTALL_SH = b"""#!/usr/bin/env bash
set -e
echo "[dev-mock] install.sh args: $*"
zdm_ip=""
zdm_user=""
while [ "$#" -gt 0 ]; do
    case "$1" in
        -s) zdm_ip="$2"; shift 2 ;;
        -u) zdm_user="$2"; shift 2 ;;
        *) shift ;;
    esac
done
echo "[dev-mock] ZDM_IP=${zdm_ip} ZDM_USER=${zdm_user}"
echo "[dev-mock] hostname=$(hostname) date=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
exit 0
"""


def _build_tarball() -> tuple[bytes, str]:
    """더미 ZDM 본체 패키지 in-memory build. (bytes, sha256_hex) 반환.

    결정론 보장 — 같은 입력은 같은 bytes → sha256 안정 → ETag 일관 →
    HttpZdmPackageResolver Redis cache key 안정.
    두 단계: tar 는 TarInfo.mtime=0, gzip 헤더는 GzipFile mtime=0 명시.
    `tarfile.open(mode="w:gz")` 에 mtime 키워드 없음 → tar/gzip 분리 build.
    """
    raw_tar = io.BytesIO()
    with tarfile.open(fileobj=raw_tar, mode="w") as tar:
        # 디렉토리 entry 명시 — agent `extract.c` 가 libarchive 의 `ARCHIVE_EXTRACT_NO_AUTODIR`
        # 플래그로 부모 디렉토리 자동 mkdir 비활성. dir entry 없으면 install.sh write 시 ENOENT.
        dir_info = tarfile.TarInfo(name="zconverter_install_source/")
        dir_info.type = tarfile.DIRTYPE
        dir_info.mode = 0o755
        dir_info.mtime = 0
        tar.addfile(dir_info)

        info = tarfile.TarInfo(name="zconverter_install_source/install.sh")
        info.size = len(_INSTALL_SH)
        info.mode = 0o755
        info.mtime = 0
        tar.addfile(info, io.BytesIO(_INSTALL_SH))

    gz_buf = io.BytesIO()
    with gzip.GzipFile(fileobj=gz_buf, mode="wb", mtime=0) as gz:
        gz.write(raw_tar.getvalue())
    data = gz_buf.getvalue()
    sha = hashlib.sha256(data).hexdigest()
    return data, sha


_TARBALL_BYTES, _TARBALL_SHA256 = _build_tarball()
_TARBALL_SIZE = len(_TARBALL_BYTES)
# epoch 0 — 결정론 우선. resolver 는 ETag 우선이라 Last-Modified 는 실질적으로 미참조.
_TARBALL_LAST_MODIFIED = formatdate(timeval=0, usegmt=True)
_TARBALL_ETAG = f'"{_TARBALL_SHA256}"'

logger.info(
    "dev ZDM mock tarball built — size={} sha256={}",
    _TARBALL_SIZE,
    _TARBALL_SHA256[:16] + "...",
)


@dev_zdm_mock_router.api_route(web_settings.zdm_package_path, methods=["GET", "HEAD"])
async def dev_zdm_mock_download(request: Request) -> Response:
    """dev 한정 ZDM 본체 패키지 mock. HEAD/GET 동일 헤더, GET 만 body 동봉.

    HttpZdmPackageResolver 는 HEAD 로 Content-Length + ETag 추출 → cache hit 면 sha256 재사용,
    miss 면 GET full + streaming sha256 계산. Content-Length 헤더는 명시 박아 HEAD 응답에서도
    body 길이와 무관하게 size 추출 가능.
    """
    body = b"" if request.method == "HEAD" else _TARBALL_BYTES
    return Response(
        content=body,
        media_type="application/gzip",
        headers={
            "Content-Length": str(_TARBALL_SIZE),
            "ETag": _TARBALL_ETAG,
            "Last-Modified": _TARBALL_LAST_MODIFIED,
        },
    )


# Windows 본체 mock — direct_exec(.exe). Linux .tar.gz install.sh 와 대칭.
# 유효 PE (mingw x86_64 cross-build, args echo + exit 0) — Win11 ARM 의 x64 emulation(Prism)으로 실행되어
# task.result success(exit_code 0) 까지 시연.
# 빌드: x86_64-w64-mingw32-gcc -O2 -s (zdm_mock_windows.exe).
_EXE_BYTES = (pathlib.Path(__file__).parent / "zdm_mock_windows.exe").read_bytes()
_EXE_SHA256 = hashlib.sha256(_EXE_BYTES).hexdigest()
_EXE_SIZE = len(_EXE_BYTES)
_EXE_ETAG = f'"{_EXE_SHA256}"'


@dev_zdm_mock_router.api_route(web_settings.zdm_package_path_windows, methods=["GET", "HEAD"])
async def dev_zdm_mock_download_windows(request: Request) -> Response:
    """dev 한정 ZDM Windows 본체(.exe) mock. HEAD/GET 동일 헤더, GET 만 body 동봉."""
    body = b"" if request.method == "HEAD" else _EXE_BYTES
    return Response(
        content=body,
        media_type="application/octet-stream",
        headers={
            "Content-Length": str(_EXE_SIZE),
            "ETag": _EXE_ETAG,
            "Last-Modified": _TARBALL_LAST_MODIFIED,
        },
    )
