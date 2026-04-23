import uvicorn

from config import web_settings

uvicorn.run("web.main:app", host="0.0.0.0", port=web_settings.web_port, reload=True)