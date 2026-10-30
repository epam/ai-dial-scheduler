from datetime import UTC, datetime, timedelta

from apscheduler.triggers.combining import OrTrigger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from src.lib.schemas.schedule import CronTriggerSpec, ScheduleTrigger


class EntryCronTrigger(CronTrigger):
    """A ``CronTrigger`` that keeps honouring its own ``start_date`` inside an ``OrTrigger``.

    ``OrTrigger`` passes every child the previous fire time of whichever child fired last. A plain
    ``CronTrigger`` consults ``start_date`` only when that previous fire time is ``None``, so once a
    sibling entry has fired it would stop respecting its own start date. Discarding a previous fire
    time that lies before this entry's start restores the bound without losing missed slots.

    The class path is pickled into stored scheduler job rows, so renaming or moving this class
    stops the worker from loading every multi-cron job. Keep the name and module stable.
    """

    def get_next_fire_time(self, previous_fire_time: datetime | None, now: datetime) -> datetime | None:
        if previous_fire_time is not None and self.start_date is not None and previous_fire_time < self.start_date:
            previous_fire_time = None if now < self.start_date else self.start_date - timedelta(microseconds=1)
        return super().get_next_fire_time(previous_fire_time, now)


def build_trigger(trigger: ScheduleTrigger) -> CronTrigger | DateTrigger | OrTrigger:
    """Build an APScheduler trigger from a validated ScheduleTrigger.

    A list of cron entries becomes an ``OrTrigger`` that fires at the earliest next fire time of
    any entry, so entries that coincide produce a single run. All triggers are anchored to UTC.
    """
    if trigger.cron is not None:
        return CronTrigger(**trigger.cron.trigger_kwargs())
    if trigger.crons is not None:
        return _build_crons_trigger(trigger.crons)
    return DateTrigger(run_date=trigger.date, timezone=UTC)


def _build_crons_trigger(entries: list[CronTriggerSpec]) -> CronTrigger | OrTrigger:
    if len(entries) == 1:
        return CronTrigger(**entries[0].trigger_kwargs())
    return OrTrigger([EntryCronTrigger(**entry.trigger_kwargs()) for entry in entries])
