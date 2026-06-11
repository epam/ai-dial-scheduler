from datetime import UTC, datetime
from typing import Any

from src.lib.utils.logging import logger


async def execute_scheduled_job(schedule_id: str, **properties: Any) -> None:
    """Placeholder job executed by APScheduler for a scheduled task.

    Logs the current UTC time and received kwargs for observability.
    """
    logger.info(
        "Executing scheduled job",
        schedule_id=schedule_id,
        executed_at=datetime.now(UTC).isoformat(),
        properties=properties,
    )
