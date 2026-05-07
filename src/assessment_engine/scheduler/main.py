import asyncio

from loguru import logger

from assessment_engine.config import scheduler_settings


async def run_diagnostics() -> None:
    raise NotImplementedError


async def main() -> None:
    interval = scheduler_settings.scheduler_interval_seconds
    logger.info("scheduler starting interval={}s", interval)

    loop = asyncio.get_event_loop()
    while True:
        logger.info("running diagnostics")
        start = loop.time()
        try:
            await run_diagnostics()
        except NotImplementedError:
            logger.warning("run_diagnostics not implemented yet, skipping")
        except Exception as e:
            logger.exception("diagnostics failed: {}", e)

        elapsed = loop.time() - start
        await asyncio.sleep(max(0, interval - elapsed))


