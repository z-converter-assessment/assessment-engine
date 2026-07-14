"""FastAPI OpenAPI 스펙을 파일로 덤프 — 표현계층 타입 codegen 입력.

`app.openapi()` 는 라우트 introspection 만 하고 DB/broker 에 연결하지 않는다. import 시점
Settings 인스턴스화만 통과하면 되므로 안전한 codegen 기본값을 주입한다(실행 환경 무의존).

사용: python scripts/dump_openapi.py [out.json]  (기본 out=openapi.json)
"""

import json
import os
import sys
from pathlib import Path

# 서버 기동 아님 — 스키마 introspection 전용. weak default 검증(prod)만 우회하면 됨.
os.environ.setdefault("APP_ENV", "dev")
os.environ.setdefault("SECRET_KEY", "codegen")
os.environ.setdefault("POSTGRES_PASSWORD", "codegen")
os.environ.setdefault("RABBITMQ_PASSWORD", "codegen")
os.environ.setdefault("RABBITMQ_WORKER_PASSWORD", "codegen")

from assessment_engine.web.main import app  # noqa: E402

if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "openapi.json")
    out.write_text(json.dumps(app.openapi(), ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
