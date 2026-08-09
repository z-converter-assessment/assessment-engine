import json
import sys
from pathlib import Path

from assessment_engine.web.main import app

if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "openapi.json")

    out.write_text(json.dumps(app.openapi(), ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
