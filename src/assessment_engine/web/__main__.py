from pathlib import Path

import uvicorn

import assessment_engine
from assessment_engine.web.settings import get_web_settings

# dev hot-reload — 코드가 site-packages 에 bind mount(WORKDIR /app 밖)라 cwd watch 로는 못 잡는다.
# 패키지 디렉토리를 명시 watch (reload=False 면 무시).
_reload_dirs = [str(Path(assessment_engine.__file__).parent)] if get_web_settings().web_reload else None

uvicorn.run(
    "assessment_engine.web.main:app",
    # 컨테이너 안에서 도는 프로세스라 전 인터페이스 바인딩이 정상이다 — 외부 노출 경계는 compose 포트 매핑.
    host="0.0.0.0",  # noqa: S104
    port=get_web_settings().web_port,
    reload=get_web_settings().web_reload,
    reload_dirs=_reload_dirs,
    timeout_graceful_shutdown=3,
)
