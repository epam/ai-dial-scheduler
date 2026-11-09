from datetime import UTC, datetime, timedelta, timezone
import pickle

from apscheduler.triggers.combining import OrTrigger
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


def _crons_trigger(*entries: CronTriggerSpec) -> ScheduleTrigger:
    return ScheduleTrigger(crons=list(entries))


def _weekly_entries() -> tuple[CronTriggerSpec, CronTriggerSpec]:
    return (
        CronTriggerSpec(fields=CronFields(day_of_week="mon", hour=8, minute=0)),
        CronTriggerSpec(fields=CronFields(day_of_week="fri", hour=10, minute=0)),
    )


def _fire_times(trigger: OrTrigger | CronTrigger, start: datetime, count: int) -> list[datetime]:
    """Step a trigger the way APScheduler does, feeding each fire time back as the previous one."""
    times: list[datetime] = []
    previous: datetime | None = None
    now = start
    for _ in range(count):
        fire = trigger.get_next_fire_time(previous, now)
        if fire is None:
            break
        times.append(fire)
        previous = fire
        now = fire
    return times


class TestBuildTriggerCrons:
    def test_two_entries_return_or_trigger_with_utc_children(self):
        result = build_trigger(_crons_trigger(*_weekly_entries()))
        assert isinstance(result, OrTrigger)
        assert len(result.triggers) == 2
        assert all(isinstance(child, CronTrigger) for child in result.triggers)
        assert all(str(child.timezone) == "UTC" for child in result.triggers)

    def test_single_entry_list_returns_plain_cron_trigger(self):
        result = build_trigger(_crons_trigger(_weekly_entries()[0]))
        assert isinstance(result, CronTrigger)

    def test_next_fire_time_from_friday_after_slot_is_next_monday(self):
        result = build_trigger(_crons_trigger(*_weekly_entries()))
        now = datetime(2026, 11, 6, 11, 0, tzinfo=UTC)  # Friday
        assert result.get_next_fire_time(None, now) == datetime(2026, 11, 9, 8, 0, tzinfo=UTC)

    def test_fire_sequence_alternates_between_entries(self):
        result = build_trigger(_crons_trigger(*_weekly_entries()))
        times = _fire_times(result, datetime(2026, 11, 9, 7, 0, tzinfo=UTC), 3)
        assert times == [
            datetime(2026, 11, 9, 8, 0, tzinfo=UTC),
            datetime(2026, 11, 13, 10, 0, tzinfo=UTC),
            datetime(2026, 11, 16, 8, 0, tzinfo=UTC),
        ]

    def test_four_week_window_yields_exactly_eight_fire_times(self):
        result = build_trigger(_crons_trigger(*_weekly_entries()))
        start = datetime(2026, 11, 9, 0, 0, tzinfo=UTC)  # Monday
        end = start + timedelta(weeks=4)
        times = [t for t in _fire_times(result, start, 20) if t < end]
        assert len(times) == 8

    def test_coinciding_entries_yield_one_fire_time(self):
        entry = CronTriggerSpec(fields=CronFields(hour=9, minute=0))
        result = build_trigger(_crons_trigger(entry, entry))
        times = _fire_times(result, datetime(2026, 11, 9, 0, 0, tzinfo=UTC), 3)
        assert times == [
            datetime(2026, 11, 9, 9, 0, tzinfo=UTC),
            datetime(2026, 11, 10, 9, 0, tzinfo=UTC),
            datetime(2026, 11, 11, 9, 0, tzinfo=UTC),
        ]

    def test_expired_entry_does_not_block_the_other(self):
        expired = CronTriggerSpec(
            fields=CronFields(hour=8, minute=0),
            end_date=datetime(2026, 1, 1, tzinfo=UTC),
        )
        live = CronTriggerSpec(fields=CronFields(hour=10, minute=0))
        result = build_trigger(_crons_trigger(expired, live))
        assert result.get_next_fire_time(None, datetime(2026, 11, 9, 0, 0, tzinfo=UTC)) == datetime(
            2026, 11, 9, 10, 0, tzinfo=UTC
        )

    def test_all_entries_expired_returns_none(self):
        end = datetime(2026, 1, 1, tzinfo=UTC)
        entries = [
            CronTriggerSpec(fields=CronFields(hour=8, minute=0), end_date=end),
            CronTriggerSpec(fields=CronFields(hour=10, minute=0), end_date=end),
        ]
        result = build_trigger(_crons_trigger(*entries))
        assert result.get_next_fire_time(None, datetime(2026, 11, 9, 0, 0, tzinfo=UTC)) is None

    def test_or_trigger_survives_pickle_round_trip(self):
        result = build_trigger(_crons_trigger(*_weekly_entries()))
        restored = pickle.loads(pickle.dumps(result))
        now = datetime(2026, 11, 6, 11, 0, tzinfo=UTC)
        assert restored.get_next_fire_time(None, now) == result.get_next_fire_time(None, now)


class TestBuildTriggerCronsStartDate:
    def _trigger_with_late_friday_entry(self) -> OrTrigger:
        entries = (
            CronTriggerSpec(fields=CronFields(day_of_week="mon", hour=8, minute=0)),
            CronTriggerSpec(
                fields=CronFields(day_of_week="fri", hour=10, minute=0),
                start_date=datetime(2026, 11, 20, tzinfo=UTC),
            ),
        )
        result = build_trigger(_crons_trigger(*entries))
        assert isinstance(result, OrTrigger)
        return result

    def test_entry_start_date_is_honoured_after_a_sibling_fires(self):
        result = self._trigger_with_late_friday_entry()
        monday = datetime(2026, 11, 2, 8, 0, tzinfo=UTC)
        times = _fire_times(result, monday - timedelta(hours=1), 4)
        assert times == [
            monday,
            datetime(2026, 11, 9, 8, 0, tzinfo=UTC),
            datetime(2026, 11, 16, 8, 0, tzinfo=UTC),
            datetime(2026, 11, 20, 10, 0, tzinfo=UTC),
        ]

    def test_missed_slot_after_start_date_is_still_caught_up(self):
        result = self._trigger_with_late_friday_entry()
        previous = datetime(2026, 11, 16, 8, 0, tzinfo=UTC)
        now = datetime(2026, 11, 20, 11, 0, tzinfo=UTC)
        assert result.get_next_fire_time(previous, now) == datetime(2026, 11, 20, 10, 0, tzinfo=UTC)

    def test_start_date_trigger_survives_pickle_round_trip(self):
        result = self._trigger_with_late_friday_entry()
        restored = pickle.loads(pickle.dumps(result))
        monday = datetime(2026, 11, 2, 8, 0, tzinfo=UTC)
        assert restored.get_next_fire_time(monday, monday) == result.get_next_fire_time(monday, monday)
        assert restored.get_next_fire_time(monday, monday) == datetime(2026, 11, 9, 8, 0, tzinfo=UTC)
