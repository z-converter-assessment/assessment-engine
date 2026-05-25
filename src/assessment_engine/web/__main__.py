import uvicorn

from assessment_engine.web.settings import web_settings

uvicorn.run(
    "assessment_engine.web.main:app",
    host="0.0.0.0",
    port=web_settings.web_port,
    reload=web_settings.web_reload,
    timeout_graceful_shutdown=3,
)
