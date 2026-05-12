"""Install payload router — agent가 `curl http://{source_host}/zconverter.tar.gz`로 fetch하는 tar 번들 제공.

흐름 (#B6 Task RPC piggyback + docs/architecture/agent.md "Task RPC piggyback"):
  1. 운영자 web UI에서 서버 체크 + source_host 입력(예: `web:8000`) → POST /api/v1/tasks/install
  2. 다음 metrics 발행 때 consumer가 RPC piggyback으로 task 명령 회신 (params={"source_host": ...})
  3. agent가 `http://{source_host}/zconverter.tar.gz` fetch → `tar -xzf` → `bash install.sh`
  4. agent가 task.result 큐로 결과 보고

본 endpoint는 본 엔진이 직접 호스팅하는 install 번들. 운영자가 source_host에 엔진 host를 입력하면 agent fetch URL이
본 endpoint가 된다. source_host가 외부 호스트를 가리키면 agent는 외부 tar를 fetch — 본 endpoint와 무관.

F17 예외 — agent 계약 endpoint는 URL versioning(`/api/v1/`) 없음. agent.md의 hardcoded path 계약 우선.
"""
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

# tar 안 파일명 — agent.md "Task RPC piggyback" 절의 agent 동작 (`bash install.sh`) 계약 일치.
_SCRIPT_NAME = "install.sh"
# 실행 권한 — tar 안 메타로 박혀서 agent의 `tar -xzf`가 권한 그대로 복원.
_SCRIPT_MODE = 0o755


def _build_install_bundle() -> bytes:
    """in-memory tar.gz 생성. 매 요청마다 같은 결과 (mtime epoch 고정).

    tarfile.TarInfo에 명시:
    - mode: 실행 권한 (0o755 → -rwxr-xr-x). agent 측 `tar -xzf`가 권한 복원.
    - uid/gid: 0 (root) — tar 안 메타. agent가 풀 때 자기 umask·user로 적용되므로 의미 작지만 명시.
    - mtime: 0 (epoch) — 같은 코드면 같은 bytes 보장. cache·디버그 친화.
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", compresslevel=6) as tar:
        info = tarfile.TarInfo(name=_SCRIPT_NAME)
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
_BUNDLE_CACHE = _build_install_bundle()


@payloads_router.get("/zconverter.tar.gz")
async def get_install_bundle() -> Response:
    """agent의 hardcoded fetch path. agent.md "Task RPC piggyback" 절 계약 일치.

    인증 없음 — 폐쇄망 가정 (#F12). 외부 노출 시 별도 ADR로 인증·rate limit 도입.
    """
    return Response(
        content=_BUNDLE_CACHE,
        media_type="application/gzip",
        headers={
            "Content-Disposition": 'attachment; filename="zconverter.tar.gz"',
            "Cache-Control": "no-cache",  # 운영자 install.sh 수정 후 재시작 시 즉시 반영
        },
    )
