from datetime import UTC, datetime, timedelta, tzinfo
from typing import Any, cast
from zoneinfo import ZoneInfo

from apscheduler.triggers.combining import OrTrigger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from src.lib.schemas.schedule import CronTriggerSpec, ScheduleTrigger


# Longest clock change the search accounts for. Real-world changes are at most one hour today;
# two leaves headroom for historical double summer time.
_MAX_CLOCK_CHANGE = timedelta(hours=2)
_TICK = timedelta(microseconds=1)


class WallClockCronTrigger(CronTrigger):
    """A ``CronTrigger`` whose fire times follow local wall-clock time correctly across DST changes.

    APScheduler 3.x steps cron fields by adding wall-clock differences to real timestamps, which
    breaks around a clock change in any zone with daylight saving time: for example a daily 00:59
    entry in ``Europe/Berlin`` skips the whole day after clocks go forward, and a daily 02:30 entry
    fires twice when they go back. This trigger therefore lets APScheduler step only through naive
    wall-clock times (a UTC copy of the same fields, where no clock changes exist) and maps every
    matching local time to instants itself:

    - a local time that does not exist (clocks going forward) fires once, shifted forward by the
      length of the gap (02:30 fires at 03:30);
    - a local time that occurs twice (clocks going back) fires once, at the first occurrence, unless
      the hour field matches all 24 hours (``*``, ``*/1``, ``0-23``), in which case both passes fire
      so every real elapsed hour is covered.

    The result is the earliest such instant at or after the lower bound, so a shifted slot that
    coincides with a real one fires once. ``start_date`` is honoured on every call, not only when
    there is no previous fire time.

    The hour field is probed for every hour rather than read through ``is_default``, because
    APScheduler also marks an unspecified hour as default when it fills it with ``0`` (for example
    ``day=1``), which is a fixed time, not "every hour".

    The class path is pickled into stored scheduler job rows, so renaming or moving this class
    stops the worker from loading every cron job built from it. Keep the name and module stable.
    """

    def get_next_fire_time(self, previous_fire_time: datetime | None, now: datetime) -> datetime | None:
        """Return the earliest valid fire instant after ``previous_fire_time`` (or from ``now``)."""
        # As in CronTrigger (which uses min(now, previous + 1µs), with previous <= now in practice):
        # catch up from just after the previous fire time, never repeat it.
        lower = now.astimezone(UTC) if previous_fire_time is None else previous_fire_time.astimezone(UTC) + _TICK
        if self.start_date is not None:
            lower = max(lower, self.start_date.astimezone(UTC))
        fire_time = self._earliest_instant_from(lower)
        if fire_time is None or (self.end_date is not None and fire_time.timestamp() > self.end_date.timestamp()):
            return None
        return self._apply_jitter(fire_time, self.jitter, now)

    def _earliest_instant_from(self, lower: datetime) -> datetime | None:
        zone = self.timezone
        wall_steps = self._wall_clock_trigger()
        # Near a clock change, wall-clock readings and instants are not in the same order: a slot
        # shifted out of a forward gap, or the second pass of a repeated hour, has an earlier reading
        # than instants before it. The scan then starts early enough to see those slots.
        wall = _naive_wall(lower, zone) - _reorder_window(lower, zone)
        best: datetime | None = None
        horizon: datetime | None = None
        while (wall_match := _next_wall(wall_steps, wall)) is not None:
            if horizon is not None and wall_match > horizon:
                break
            for instant in self._instants_for(wall_match):
                # Timestamps, not datetimes: same-zone datetimes compare by wall-clock fields and
                # ignore ``fold``, which misorders instants around a clock change.
                stamp = instant.timestamp()
                if stamp >= lower.timestamp() and (best is None or stamp < best.timestamp()):
                    best = instant
            if best is not None and horizon is None:
                horizon = wall_match + _reorder_window(best, zone)
            wall = wall_match + _TICK
        return best

    def _instants_for(self, wall: datetime) -> list[datetime]:
        zone = self.timezone
        first = wall.replace(tzinfo=zone, fold=0)
        second = wall.replace(tzinfo=zone, fold=1)
        first_offset, second_offset = first.utcoffset(), second.utcoffset()
        if first_offset == second_offset:
            return [first]
        # fold=0 resolves a missing local time with the offset in force before the gap, which is
        # exactly the instant the slot shifts to.
        instants = [_canonical(first, zone)]
        repeated = first_offset is not None and second_offset is not None and first_offset > second_offset
        if repeated and self._fires_every_hour():
            instants.append(_canonical(second, zone))
        return instants

    def _fires_every_hour(self) -> bool:
        hour_field = self.fields[self.FIELD_NAMES.index("hour")]
        probe = datetime(2000, 1, 1)
        return all(hour_field.get_next_value(probe.replace(hour=hour)) == hour for hour in range(24))

    def _wall_clock_trigger(self) -> CronTrigger:
        stepper = CronTrigger.__new__(CronTrigger)
        own_state = cast(dict[str, Any], self.__getstate__())
        state = {**own_state, "timezone": UTC, "start_date": None, "end_date": None, "jitter": None}
        stepper.__setstate__(state)
        return stepper


class EntryCronTrigger(WallClockCronTrigger):
    """One entry of a multi-cron schedule inside an ``InstantOrTrigger``.

    ``OrTrigger`` passes every child the previous fire time of whichever child fired last, and a
    plain ``CronTrigger`` would then ignore its own ``start_date``. ``WallClockCronTrigger`` now
    honours ``start_date`` on every call, so this class adds no behaviour of its own.

    It is kept because its class path is pickled into stored scheduler job rows of multi-cron
    schedules created before per-schedule timezones; renaming or moving it stops the worker from
    loading those jobs. Keep the name and module stable.
    """


class InstantOrTrigger(OrTrigger):
    """An ``OrTrigger`` that picks the earliest child fire time by instant.

    ``OrTrigger`` uses ``min()`` on the children's fire times. They all share the schedule's zone,
    and Python compares same-zone datetimes by wall-clock fields, ignoring the UTC offset and
    ``fold``. Around a daylight-saving change that picks a later instant and silently drops an
    earlier slot, so this trigger compares timestamps instead.

    The class path is pickled into stored scheduler job rows, so renaming or moving this class
    stops the worker from loading every multi-cron job. Keep the name and module stable.
    """

    __slots__ = ()

    def get_next_fire_time(self, previous_fire_time: datetime | None, now: datetime) -> datetime | None:
        """Return the earliest next fire time of any child, compared as UTC instants."""
        fire_times = [trigger.get_next_fire_time(previous_fire_time, now) for trigger in self.triggers]
        pending = [fire_time for fire_time in fire_times if fire_time is not None]
        if not pending:
            return None
        return self._apply_jitter(min(pending, key=datetime.timestamp), self.jitter, now)


def _offset(instant: datetime, zone: tzinfo) -> timedelta:
    return instant.astimezone(zone).utcoffset() or timedelta(0)


def _naive_wall(instant: datetime, zone: tzinfo) -> datetime:
    return instant.astimezone(zone).replace(tzinfo=None)


def _canonical(local: datetime, zone: tzinfo) -> datetime:
    return datetime.fromtimestamp(local.timestamp(), zone)


def _next_wall(wall_steps: CronTrigger, wall: datetime) -> datetime | None:
    match = wall_steps.get_next_fire_time(None, wall.replace(tzinfo=UTC))
    return None if match is None else match.replace(tzinfo=None)


def _reorder_window(instant: datetime, zone: tzinfo) -> timedelta:
    """How far wall-clock order may disagree with instant order around ``instant``.

    The disagreement never exceeds the size of a clock change, and only exists within that distance
    of one, so it is zero away from changes and the scan stops at the first match.
    """
    return abs(_offset(instant + _MAX_CLOCK_CHANGE, zone) - _offset(instant - _MAX_CLOCK_CHANGE, zone))


def build_trigger(trigger: ScheduleTrigger, timezone: str) -> CronTrigger | DateTrigger | OrTrigger:
    """Build an APScheduler trigger from a validated ScheduleTrigger, evaluated in ``timezone``.

    Cron fields and naive datetimes are local to ``timezone`` (an IANA identifier already validated
    by the request schema). A list of cron entries becomes an ``InstantOrTrigger`` that fires at the
    earliest next fire time of any entry, so entries that coincide produce a single run.
    """
    zone = ZoneInfo(timezone)
    if trigger.cron is not None:
        return WallClockCronTrigger(**trigger.cron.trigger_kwargs(zone))
    if trigger.crons is not None:
        return _build_crons_trigger(trigger.crons, zone)
    return DateTrigger(run_date=trigger.date, timezone=zone)


def _build_crons_trigger(entries: list[CronTriggerSpec], zone: ZoneInfo) -> CronTrigger | OrTrigger:
    if len(entries) == 1:
        return WallClockCronTrigger(**entries[0].trigger_kwargs(zone))
    return InstantOrTrigger([EntryCronTrigger(**entry.trigger_kwargs(zone)) for entry in entries])
