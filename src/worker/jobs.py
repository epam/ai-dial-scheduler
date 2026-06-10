from src.lib.utils.logging import logger


async def dummy_job(message: str) -> None:
    logger.info("Dummy job executed", message=message)
