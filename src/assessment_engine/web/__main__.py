import os

import uvicorn

import assessment_engine
from assessment_engine.web.settings import get_web_settings

# dev hot-reload — 코드가 site-packages 에 bind mount(WORKDIR /app 밖)라 cwd watch 로는 못 잡는다.
# 패키지 디렉토리를 명시 watch (reload=False 면 무시).
_reload_dirs = [os.path.dirname(assessment_engine.__file__)] if get_web_settings().web_reload else None

uvicorn.run(
    "assessment_engine.web.main:app",
    host="0.0.0.0",
    port=get_web_settings().web_port,
    reload=get_web_settings().web_reload,
    reload_dirs=_reload_dirs,
    timeout_graceful_shutdown=3,
)
