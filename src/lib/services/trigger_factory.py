from datetime import UTC

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from src.lib.schemas.schedule import ScheduleTrigger


def build_trigger(trigger: ScheduleTrigger) -> CronTrigger | DateTrigger:
    """Build an APScheduler trigger from a validated ScheduleTrigger.

    All triggers are anchored to UTC.
    """
    if trigger.cron is not None:
        spec = trigger.cron
        fields = {k: v for k, v in spec.fields.model_dump().items() if v is not None}
        return CronTrigger(
            timezone=UTC,
            start_date=spec.start_date,
            end_date=spec.end_date,
            **fields,
        )
    return DateTrigger(run_date=trigger.date, timezone=UTC)
