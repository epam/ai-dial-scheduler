class SchedulerError(Exception):
    """Base for all scheduler domain exceptions."""


class ScheduleNotFoundError(SchedulerError):
    """Raised when a schedule is not found or not owned by the caller."""


class JobRegistrationError(SchedulerError):
    """Raised when APScheduler fails to register a job after row persistence."""


class ScheduleRunError(SchedulerError):
    """Raised when a scheduled run fails; carries schedule_id and run_id for correlation."""

    def __init__(self, message: str, schedule_id: str, run_id: str) -> None:
        super().__init__(message)
        self.schedule_id = schedule_id
        self.run_id = run_id
