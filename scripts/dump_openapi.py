"""OpenAPI 스펙을 JSON 으로 덤프한다.

이 JSON 을 openapi-typescript 가 읽어 클라이언트 JS 가 참조할 TS 타입을 만든다
(`pnpm run codegen` 이 두 단계를 이어 실행한다).

설정도 DB 도 필요 없다. Settings 는 요청·기동 시점에만 만들어지므로 import 와 스키마 생성이
값을 요구하지 않는다.

사용: python scripts/dump_openapi.py [out.json]   (기본 openapi.json)
"""

import json
import sys
from pathlib import Path

from assessment_engine.web.main import app

if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "openapi.json")
    # 키 정렬 — 같은 라우트에서 늘 같은 바이트가 나와야 워크플로의 drift 대조가 성립한다.
    out.write_text(json.dumps(app.openapi(), ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
