import uvicorn

from assessment_engine.config import web_settings

uvicorn.run(
    "assessment_engine.web.main:app",
    host="0.0.0.0",
    port=web_settings.web_port,
    reload=True,
    timeout_graceful_shutdown=3,
)