import asyncio

from sqlalchemy import text

from src.lib.db.engine import Database
from src.lib.utils.logging import logger


async def check_database(timeout: float) -> bool:
    """Return True if the database responds to a trivial query within *timeout* seconds."""
    try:

        async def _query() -> None:
            async with Database().sessionmaker() as session:
                await session.execute(text("SELECT 1"))

        await asyncio.wait_for(_query(), timeout=timeout)
        return True
    except Exception as exc:
        logger.warning("readiness_check_failed", error=str(exc))
        return False
