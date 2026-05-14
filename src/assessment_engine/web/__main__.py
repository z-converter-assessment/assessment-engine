import asyncio

import uvicorn

from assessment_engine.config import web_settings

# 2-port 분리 (ADR 0008 임시) — install bundle endpoint(/zconverter.tar.gz)만 HTTPS,
# 나머지(브라우저·API·healthcheck)는 plain HTTP. uvicorn single-port 도구라 한 process 안에서
# Server 2 instance + asyncio.gather 로 두 port 동시 listen. 정석은 agent 측 dev http toggle
# 또는 nginx ingress sidecar — 별도 ADR.
#
# ssl_certfile / ssl_keyfile 미주입 시 HTTPS port skip (prod 외부 ingress 호환).


def _server(host: str, port: int, **kwargs) -> uvicorn.Server:
    config = uvicorn.Config(
        "assessment_engine.web.main:app",
        host=host,
        port=port,
        reload=True,
        timeout_graceful_shutdown=3,
        **kwargs,
    )
    return uvicorn.Server(config)


async def _run() -> None:
    servers = [_server("0.0.0.0", web_settings.web_port).serve()]
    if web_settings.ssl_certfile and web_settings.ssl_keyfile:
        servers.append(
            _server(
                "0.0.0.0",
                web_settings.https_port,
                ssl_certfile=web_settings.ssl_certfile,
                ssl_keyfile=web_settings.ssl_keyfile,
            ).serve()
        )
    await asyncio.gather(*servers)


asyncio.run(_run())
