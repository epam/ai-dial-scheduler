from datetime import UTC, datetime, timedelta, timezone
import pickle
from zoneinfo import ZoneInfo

from apscheduler.triggers.combining import OrTrigger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
import pytest

from src.lib.schemas.schedule import CronFields, CronTriggerSpec, ScheduleTrigger
from src.lib.services.trigger_factory import EntryCronTrigger, InstantOrTrigger, WallClockCronTrigger, build_trigger


class TestBuildTrigger:
    def test_cron_spec_returns_cron_trigger(self):
        trigger = ScheduleTrigger(cron=CronTriggerSpec(fields=CronFields(hour=9, minute=0)))
        result = build_trigger(trigger, "UTC")
        assert isinstance(result, CronTrigger)

    def test_cron_trigger_is_utc(self):
        trigger = ScheduleTrigger(cron=CronTriggerSpec(fields=CronFields(hour=9)))
        result = build_trigger(trigger, "UTC")
        assert isinstance(result, CronTrigger)
        assert str(result.timezone) == "UTC"

    def test_cron_with_start_end_dates(self):
        start = datetime(2026, 6, 1, tzinfo=timezone.utc)
        end = datetime(2026, 12, 31, tzinfo=timezone.utc)
        trigger = ScheduleTrigger(cron=CronTriggerSpec(fields=CronFields(hour=9), start_date=start, end_date=end))
        result = build_trigger(trigger, "UTC")
        assert isinstance(result, CronTrigger)

    def test_date_trigger_returns_date_trigger(self):
        run_date = datetime(2026, 6, 12, 9, 0, tzinfo=timezone.utc)
        trigger = ScheduleTrigger(date=run_date)
        result = build_trigger(trigger, "UTC")
        assert isinstance(result, DateTrigger)

    def test_date_trigger_is_utc(self):
        run_date = datetime(2026, 6, 12, 9, 0, tzinfo=timezone.utc)
        trigger = ScheduleTrigger(date=run_date)
        result = build_trigger(trigger, "UTC")
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
        result = build_trigger(_crons_trigger(*_weekly_entries()), "UTC")
        assert isinstance(result, OrTrigger)
        assert len(result.triggers) == 2
        assert all(isinstance(child, CronTrigger) for child in result.triggers)
        assert all(str(child.timezone) == "UTC" for child in result.triggers)

    def test_single_entry_list_returns_plain_cron_trigger(self):
        result = build_trigger(_crons_trigger(_weekly_entries()[0]), "UTC")
        assert isinstance(result, CronTrigger)

    def test_next_fire_time_from_friday_after_slot_is_next_monday(self):
        result = build_trigger(_crons_trigger(*_weekly_entries()), "UTC")
        now = datetime(2026, 11, 6, 11, 0, tzinfo=UTC)  # Friday
        assert result.get_next_fire_time(None, now) == datetime(2026, 11, 9, 8, 0, tzinfo=UTC)

    def test_fire_sequence_alternates_between_entries(self):
        result = build_trigger(_crons_trigger(*_weekly_entries()), "UTC")
        times = _fire_times(result, datetime(2026, 11, 9, 7, 0, tzinfo=UTC), 3)
        assert times == [
            datetime(2026, 11, 9, 8, 0, tzinfo=UTC),
            datetime(2026, 11, 13, 10, 0, tzinfo=UTC),
            datetime(2026, 11, 16, 8, 0, tzinfo=UTC),
        ]

    def test_four_week_window_yields_exactly_eight_fire_times(self):
        result = build_trigger(_crons_trigger(*_weekly_entries()), "UTC")
        start = datetime(2026, 11, 9, 0, 0, tzinfo=UTC)  # Monday
        end = start + timedelta(weeks=4)
        times = [t for t in _fire_times(result, start, 20) if t < end]
        assert len(times) == 8

    def test_coinciding_entries_yield_one_fire_time(self):
        entry = CronTriggerSpec(fields=CronFields(hour=9, minute=0))
        result = build_trigger(_crons_trigger(entry, entry), "UTC")
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
        result = build_trigger(_crons_trigger(expired, live), "UTC")
        assert result.get_next_fire_time(None, datetime(2026, 11, 9, 0, 0, tzinfo=UTC)) == datetime(
            2026, 11, 9, 10, 0, tzinfo=UTC
        )

    def test_all_entries_expired_returns_none(self):
        end = datetime(2026, 1, 1, tzinfo=UTC)
        entries = [
            CronTriggerSpec(fields=CronFields(hour=8, minute=0), end_date=end),
            CronTriggerSpec(fields=CronFields(hour=10, minute=0), end_date=end),
        ]
        result = build_trigger(_crons_trigger(*entries), "UTC")
        assert result.get_next_fire_time(None, datetime(2026, 11, 9, 0, 0, tzinfo=UTC)) is None

    def test_or_trigger_survives_pickle_round_trip(self):
        result = build_trigger(_crons_trigger(*_weekly_entries()), "UTC")
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
        result = build_trigger(_crons_trigger(*entries), "UTC")
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


BERLIN = ZoneInfo("Europe/Berlin")


def _utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)  # type: ignore[misc]


class TestBuildTriggerTimezone:
    def test_single_cron_carries_zone(self):
        result = build_trigger(ScheduleTrigger(cron=CronTriggerSpec(fields=CronFields(hour=9))), "Europe/Berlin")
        assert isinstance(result, WallClockCronTrigger)
        assert result.timezone == BERLIN

    def test_one_entry_crons_carries_zone(self):
        result = build_trigger(_crons_trigger(_weekly_entries()[0]), "Europe/Berlin")
        assert isinstance(result, WallClockCronTrigger)
        assert result.timezone == BERLIN

    def test_every_crons_child_carries_zone(self):
        result = build_trigger(_crons_trigger(*_weekly_entries()), "Europe/Berlin")
        assert isinstance(result, OrTrigger)
        assert all(isinstance(child, EntryCronTrigger) for child in result.triggers)
        assert all(child.timezone == BERLIN for child in result.triggers)

    def test_date_trigger_carries_zone(self):
        result = build_trigger(ScheduleTrigger(date=datetime(2027, 1, 1, 9, 0)), "Europe/Berlin")
        assert isinstance(result, DateTrigger)
        assert result.run_date == _utc(2027, 1, 1, 8, 0)

    def test_aware_date_keeps_its_instant(self):
        result = build_trigger(ScheduleTrigger(date=_utc(2027, 1, 1, 9, 0)), "Europe/Berlin")
        assert isinstance(result, DateTrigger)
        assert result.run_date == _utc(2027, 1, 1, 9, 0)

    def test_naive_start_date_is_local_to_zone(self):
        spec = CronTriggerSpec(fields=CronFields(hour=9), start_date=datetime(2027, 1, 1, 0, 0))
        result = build_trigger(ScheduleTrigger(cron=spec), "Europe/Berlin")
        assert isinstance(result, CronTrigger)
        assert result.start_date == _utc(2026, 12, 31, 23, 0)

    def test_aware_start_date_keeps_its_instant(self):
        spec = CronTriggerSpec(fields=CronFields(hour=9), start_date=_utc(2027, 1, 1, 0, 0))
        result = build_trigger(ScheduleTrigger(cron=spec), "Europe/Berlin")
        assert isinstance(result, CronTrigger)
        assert result.start_date == _utc(2027, 1, 1, 0, 0)

    def test_local_nine_oclock_follows_spring_dst_change(self):
        result = build_trigger(ScheduleTrigger(cron=CronTriggerSpec(fields=CronFields(hour=9))), "Europe/Berlin")
        assert result.get_next_fire_time(None, _utc(2027, 3, 26, 0, 0)) == _utc(2027, 3, 26, 8, 0)
        assert result.get_next_fire_time(None, _utc(2027, 3, 29, 0, 0)) == _utc(2027, 3, 29, 7, 0)

    def test_half_hour_offset_zone(self):
        result = build_trigger(ScheduleTrigger(cron=CronTriggerSpec(fields=CronFields(hour=9))), "Asia/Kolkata")
        assert result.get_next_fire_time(None, _utc(2026, 11, 4, 0, 0)) == _utc(2026, 11, 4, 3, 30)

    def test_crons_entries_fire_at_local_times(self):
        result = build_trigger(_crons_trigger(*_weekly_entries()), "America/New_York")
        times = _fire_times(result, _utc(2026, 11, 9, 0, 0), 2)
        assert times == [_utc(2026, 11, 9, 13, 0), _utc(2026, 11, 13, 15, 0)]

    def test_utc_fire_times_match_plain_cron_trigger(self):
        fields = CronFields(day_of_week="mon-fri", hour=9, minute=30)
        result = build_trigger(ScheduleTrigger(cron=CronTriggerSpec(fields=fields)), "UTC")
        plain = CronTrigger(day_of_week="mon-fri", hour=9, minute=30, timezone=UTC)
        start = _utc(2026, 11, 2, 0, 0)
        assert _fire_times(result, start, 20) == _fire_times(plain, start, 20)

    def test_zoned_trigger_survives_pickle_round_trip(self):
        result = build_trigger(ScheduleTrigger(cron=CronTriggerSpec(fields=CronFields(hour=9))), "Europe/Kiev")
        restored = pickle.loads(pickle.dumps(result))
        assert type(restored) is WallClockCronTrigger
        assert str(restored.timezone) == "Europe/Kiev"

    def test_zoned_or_trigger_survives_pickle_round_trip(self):
        result = build_trigger(_crons_trigger(*_weekly_entries()), "Europe/Kiev")
        restored = pickle.loads(pickle.dumps(result))
        assert all(type(child) is EntryCronTrigger for child in restored.triggers)
        assert all(str(child.timezone) == "Europe/Kiev" for child in restored.triggers)
        now = _utc(2026, 11, 6, 11, 0)
        assert restored.get_next_fire_time(None, now) == result.get_next_fire_time(None, now)


def _daily(hour: int | str, minute: int | str = 0) -> CronTrigger:
    spec = CronTriggerSpec(fields=CronFields(hour=hour, minute=minute))
    trigger = build_trigger(ScheduleTrigger(cron=spec), "Europe/Berlin")
    assert isinstance(trigger, CronTrigger)
    return trigger


def _utc_fire_times(trigger: OrTrigger | CronTrigger, start: datetime, count: int) -> list[datetime]:
    """Fire times converted to UTC.

    PEP 495 makes ``==`` between aware datetimes of different zones always false when one of them
    falls in a repeated or skipped local hour, so DST assertions compare UTC instants instead.
    """
    return [fire.astimezone(UTC) for fire in _fire_times(trigger, start, count)]


class TestBuildTriggerDaylightSaving:
    def test_missing_local_time_fires_once_shifted_forward(self):
        assert _utc_fire_times(_daily(2, 30), _utc(2027, 3, 27, 12, 0), 2) == [
            _utc(2027, 3, 28, 1, 30),  # 03:30 CEST: 02:30 does not exist that night
            _utc(2027, 3, 29, 0, 30),
        ]

    def test_repeated_local_time_fires_once(self):
        assert _utc_fire_times(_daily(2, 30), _utc(2027, 10, 30, 12, 0), 2) == [
            _utc(2027, 10, 31, 0, 30),  # first 02:30, CEST
            _utc(2027, 11, 1, 1, 30),
        ]

    def test_hourly_fires_every_real_hour_through_repeated_hour(self):
        hourly = build_trigger(ScheduleTrigger(cron=CronTriggerSpec(fields=CronFields(minute=0))), "Europe/Berlin")
        assert _utc_fire_times(hourly, _utc(2027, 10, 30, 23, 30), 4) == [
            _utc(2027, 10, 31, 0, 0),  # 02:00 CEST
            _utc(2027, 10, 31, 1, 0),  # 02:00 CET
            _utc(2027, 10, 31, 2, 0),
            _utc(2027, 10, 31, 3, 0),
        ]

    def test_default_hour_zero_is_not_treated_as_every_hour(self):
        # Azores falls back from 01:00 to 00:00, so a Sunday-midnight entry (hour left at its
        # default 0) sees 00:00 twice on 2027-10-31 and must fire only for the first one.
        spec = CronTriggerSpec(fields=CronFields(day_of_week="sun"))
        weekly = build_trigger(ScheduleTrigger(cron=spec), "Atlantic/Azores")
        assert _utc_fire_times(weekly, _utc(2027, 10, 30, 12, 0), 2) == [
            _utc(2027, 10, 31, 0, 0),
            _utc(2027, 11, 7, 1, 0),
        ]

    def test_narrow_hour_range_skips_second_pass_of_repeated_hour(self):
        assert _utc_fire_times(_daily("1-3", "*/30"), _utc(2027, 10, 30, 22, 50), 6) == [
            _utc(2027, 10, 30, 23, 0),
            _utc(2027, 10, 30, 23, 30),
            _utc(2027, 10, 31, 0, 0),
            _utc(2027, 10, 31, 0, 30),
            _utc(2027, 10, 31, 2, 0),  # 03:00 CET; the second 02:00 and 02:30 are skipped
            _utc(2027, 10, 31, 2, 30),
        ]

    def test_or_trigger_children_skip_repeated_local_time(self):
        entries = (
            CronTriggerSpec(fields=CronFields(hour=2, minute=30)),
            CronTriggerSpec(fields=CronFields(day_of_week="mon", hour=8, minute=0)),
        )
        result = build_trigger(_crons_trigger(*entries), "Europe/Berlin")
        assert _utc_fire_times(result, _utc(2027, 10, 30, 12, 0), 3) == [
            _utc(2027, 10, 31, 0, 30),
            _utc(2027, 11, 1, 1, 30),
            _utc(2027, 11, 1, 7, 0),
        ]


def _berlin_crons(*fields: CronFields) -> OrTrigger | CronTrigger:
    return build_trigger(_crons_trigger(*(CronTriggerSpec(fields=f) for f in fields)), "Europe/Berlin")


class TestBuildTriggerCronsDaylightSaving:
    """Multi-entry schedules must order child fire times by instant, not by local wall-clock fields."""

    def test_or_trigger_class_is_instant_ordered(self):
        result = _berlin_crons(CronFields(hour=8), CronFields(hour=10))
        assert type(result) is InstantOrTrigger

    def test_spring_entry_after_gap_is_not_lost(self):
        result = _berlin_crons(CronFields(hour=2, minute=30), CronFields(hour=3, minute=15))
        assert _utc_fire_times(result, _utc(2027, 3, 27, 12, 0), 3) == [
            _utc(2027, 3, 28, 1, 15),  # 03:15 CEST
            _utc(2027, 3, 28, 1, 30),  # 02:30 shifted to 03:30 CEST
            _utc(2027, 3, 29, 0, 30),
        ]

    def test_fall_fixed_entry_still_fires_beside_hourly_entry(self):
        result = _berlin_crons(CronFields(minute=0), CronFields(hour=2, minute=30))
        assert _utc_fire_times(result, _utc(2027, 10, 30, 23, 30), 5) == [
            _utc(2027, 10, 31, 0, 0),  # 02:00 CEST
            _utc(2027, 10, 31, 0, 30),  # 02:30 CEST, once
            _utc(2027, 10, 31, 1, 0),  # 02:00 CET
            _utc(2027, 10, 31, 2, 0),
            _utc(2027, 10, 31, 3, 0),
        ]

    def test_fall_two_hourly_entries_keep_every_real_slot(self):
        result = _berlin_crons(CronFields(minute=0), CronFields(minute=30))
        assert _utc_fire_times(result, _utc(2027, 10, 30, 23, 50), 6) == [
            _utc(2027, 10, 31, 0, 0),
            _utc(2027, 10, 31, 0, 30),
            _utc(2027, 10, 31, 1, 0),
            _utc(2027, 10, 31, 1, 30),
            _utc(2027, 10, 31, 2, 0),
            _utc(2027, 10, 31, 2, 30),
        ]

    def test_instant_ordered_or_trigger_survives_pickle_round_trip(self):
        result = _berlin_crons(CronFields(minute=0), CronFields(hour=2, minute=30))
        restored = pickle.loads(pickle.dumps(result))
        assert type(restored) is InstantOrTrigger
        start = _utc(2027, 10, 30, 23, 30)
        assert _utc_fire_times(restored, start, 5) == _utc_fire_times(result, start, 5)

    def test_entry_start_date_inside_repeated_hour_compares_instants(self):
        # Start at the first 02:15 (00:15Z). A previous fire at the second 02:00 (01:00Z) is after
        # the start in real time even though its wall-clock reading is earlier, so it must be kept.
        entry = EntryCronTrigger(minute="0,30", timezone=BERLIN, start_date=datetime(2027, 10, 31, 2, 15))
        previous = _utc(2027, 10, 31, 1, 0).astimezone(BERLIN)
        assert entry.get_next_fire_time(previous, previous).astimezone(UTC) == _utc(2027, 10, 31, 1, 30)


class TestEveryHourDetection:
    @pytest.mark.parametrize("hour", ["*", "*/1", "0-23"])
    def test_every_hour_expressions_fire_through_repeated_hour(self, hour: str):
        assert _utc_fire_times(_daily(hour, 0), _utc(2027, 10, 30, 23, 30), 3) == [
            _utc(2027, 10, 31, 0, 0),
            _utc(2027, 10, 31, 1, 0),
            _utc(2027, 10, 31, 2, 0),
        ]

    @pytest.mark.parametrize("hour", ["*/2", "0-22", "2"])
    def test_partial_hour_expressions_skip_repeated_pass(self, hour: str):
        times = _utc_fire_times(_daily(hour, 0), _utc(2027, 10, 30, 23, 30), 2)
        assert _utc(2027, 10, 31, 1, 0) not in times  # the second 02:00 (CET) is skipped


class TestSingleEntryGapDuplicates:
    def test_shifted_slot_and_real_slot_at_same_instant_fire_once(self):
        # On 2027-03-28, 02:30 is shifted to 03:30 CEST, the same instant as the real 03:30 slot.
        assert _utc_fire_times(_daily("2,3", 30), _utc(2027, 3, 27, 12, 0), 3) == [
            _utc(2027, 3, 28, 1, 30),
            _utc(2027, 3, 29, 0, 30),
            _utc(2027, 3, 29, 1, 30),
        ]


class TestApschedulerDstSteppingRegression:
    def test_daily_entry_before_gap_does_not_skip_the_next_day(self):
        # Plain CronTrigger adds wall-clock differences to real timestamps, overshoots by the lost
        # hour and jumps from 2027-03-28 00:59 straight to 2027-03-30.
        assert _utc_fire_times(_daily(0, 59), _utc(2027, 3, 27, 17, 0), 3) == [
            _utc(2027, 3, 27, 23, 59),  # 00:59 CET
            _utc(2027, 3, 28, 22, 59),  # 00:59 CEST on 2027-03-29
            _utc(2027, 3, 29, 22, 59),
        ]

    def test_plain_cron_trigger_still_has_the_bug(self):
        """Documents why WallClockCronTrigger steps through wall-clock time itself."""
        plain = CronTrigger(hour=0, minute=59, timezone=BERLIN)
        times = _utc_fire_times(plain, _utc(2027, 3, 27, 17, 0), 2)
        assert times[1] == _utc(2027, 3, 29, 22, 59)


class TestCronsAcrossDstChangeDays:
    def test_spring_hourly_and_fixed_entry_keep_every_slot_once(self):
        result = _berlin_crons(CronFields(minute=0), CronFields(hour=2, minute=30))
        assert _utc_fire_times(result, _utc(2027, 3, 27, 23, 30), 4) == [
            _utc(2027, 3, 28, 0, 0),  # 01:00 CET
            _utc(2027, 3, 28, 1, 0),  # 03:00 CEST; the shifted 02:00 is the same instant, fired once
            _utc(2027, 3, 28, 1, 30),  # 02:30 shifted to 03:30 CEST
            _utc(2027, 3, 28, 2, 0),
        ]

    def test_new_york_weekly_entries_follow_local_time_across_spring_change(self):
        result = build_trigger(_crons_trigger(*_weekly_entries()), "America/New_York")
        assert _utc_fire_times(result, _utc(2027, 3, 8, 0, 0), 4) == [
            _utc(2027, 3, 8, 13, 0),  # Monday 08:00 EST
            _utc(2027, 3, 12, 15, 0),  # Friday 10:00 EST
            _utc(2027, 3, 15, 12, 0),  # Monday 08:00 EDT, after the 2027-03-14 change
            _utc(2027, 3, 19, 14, 0),  # Friday 10:00 EDT
        ]
