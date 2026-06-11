class SchedulerError(Exception):
    """Base for all scheduler domain exceptions."""


class ScheduleNotFoundError(SchedulerError):
    """Raised when a schedule is not found or not owned by the caller."""


class JobRegistrationError(SchedulerError):
    """Raised when APScheduler fails to register a job after row persistence."""
