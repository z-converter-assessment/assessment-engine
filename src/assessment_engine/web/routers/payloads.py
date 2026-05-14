"""Install payload router — 원격 호스트가 task.install download.url로 fetch하는 tar 번들 제공.

본 endpoint는 본 엔진이 self-host하는 install 번들. task.install 메시지의 download.url은
본 endpoint를 가리키도록 발행되고, 원격 호스트는 그 URL을 그대로 fetch한 뒤 sha256·size_bytes를
페이로드 값과 검증한다 (#F8 폐쇄망 가정, 외부 노출 시 별도 ADR로 인증·rate limit).

모듈 로드 시 bundle bytes를 1회 빌드하고 그 sha256·size를 함께 export — task.install 발행 측이
참조해 페이로드에 채운다 (단일 진실).
"""
import hashlib
import io
import tarfile

from fastapi import APIRouter
from fastapi.responses import Response

payloads_router = APIRouter(tags=["payloads"])

# install.sh 내용 — 경량 학습용. agent가 받은 후 실제 실행하는 스크립트.
# 변경 시 본 상수만 수정 → 다음 요청부터 새 tar 응답에 반영 (deterministic 안 함, mtime만 epoch 고정).
_INSTALL_SCRIPT = b"""#!/bin/bash
# Assessment Engine install bundle - lightweight learning echo script.
set -euo pipefail
echo "[install.sh] host=$(hostname) uname=$(uname -a) date=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "[install.sh] install bundle executed successfully"
"""

# tar 안 파일명. task.install 페이로드의 install.script와 일치.
INSTALL_SCRIPT_NAME = "install.sh"
# 실행 권한 — tar 안 메타로 박혀서 원격 호스트의 `tar -xzf`가 권한 그대로 복원.
_SCRIPT_MODE = 0o755


def _build_install_bundle() -> bytes:
    """in-memory tar.gz 생성. mtime epoch 고정으로 같은 코드면 같은 bytes (sha256 안정).

    tarfile.TarInfo에 명시:
    - mode: 실행 권한 (0o755 → -rwxr-xr-x).
    - uid/gid: 0 (root) — tar 안 메타.
    - mtime: 0 (epoch) — deterministic.
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", compresslevel=6) as tar:
        info = tarfile.TarInfo(name=INSTALL_SCRIPT_NAME)
        info.size = len(_INSTALL_SCRIPT)
        info.mode = _SCRIPT_MODE
        info.uid = 0
        info.gid = 0
        info.uname = "root"
        info.gname = "root"
        info.mtime = 0
        tar.addfile(info, io.BytesIO(_INSTALL_SCRIPT))
    return buf.getvalue()


# 모듈 로드 시 1회 생성 — 매 요청마다 tar build 안 함 (작은 deterministic 결과 캐시).
# 운영자가 _INSTALL_SCRIPT 수정 시 web 컨테이너 재기동(또는 uvicorn auto-reload)으로 갱신.
_BUNDLE_CACHE: bytes = _build_install_bundle()
INSTALL_BUNDLE_SIZE: int = len(_BUNDLE_CACHE)
INSTALL_BUNDLE_SHA256: str = hashlib.sha256(_BUNDLE_CACHE).hexdigest()


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
            "Cache-Control": "no-cache",  # 운영자 install.sh 수정 후 재시작 시 즉시 반영
        },
    )
