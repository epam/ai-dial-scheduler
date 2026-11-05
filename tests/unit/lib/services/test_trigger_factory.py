from datetime import datetime, timezone

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from src.lib.schemas.schedule import CronFields, CronTriggerSpec, ScheduleTrigger
from src.lib.services.trigger_factory import build_trigger


class TestBuildTrigger:
    def test_cron_spec_returns_cron_trigger(self):
        trigger = ScheduleTrigger(cron=CronTriggerSpec(fields=CronFields(hour=9, minute=0)))
        result = build_trigger(trigger)
        assert isinstance(result, CronTrigger)

    def test_cron_trigger_is_utc(self):
        trigger = ScheduleTrigger(cron=CronTriggerSpec(fields=CronFields(hour=9)))
        result = build_trigger(trigger)
        assert isinstance(result, CronTrigger)
        assert str(result.timezone) == "UTC"

    def test_cron_with_start_end_dates(self):
        start = datetime(2026, 6, 1, tzinfo=timezone.utc)
        end = datetime(2026, 12, 31, tzinfo=timezone.utc)
        trigger = ScheduleTrigger(cron=CronTriggerSpec(fields=CronFields(hour=9), start_date=start, end_date=end))
        result = build_trigger(trigger)
        assert isinstance(result, CronTrigger)

    def test_date_trigger_returns_date_trigger(self):
        run_date = datetime(2026, 6, 12, 9, 0, tzinfo=timezone.utc)
        trigger = ScheduleTrigger(date=run_date)
        result = build_trigger(trigger)
        assert isinstance(result, DateTrigger)

    def test_date_trigger_is_utc(self):
        run_date = datetime(2026, 6, 12, 9, 0, tzinfo=timezone.utc)
        trigger = ScheduleTrigger(date=run_date)
        result = build_trigger(trigger)
        assert isinstance(result, DateTrigger)
