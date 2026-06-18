class SchedulerError(Exception):
    """Base for all scheduler domain exceptions."""


class ScheduleNotFoundError(SchedulerError):
    """Raised when a schedule is not found or not owned by the caller."""


class JobRegistrationError(SchedulerError):
    """Raised when APScheduler fails to register a job after row persistence."""


class ScheduleRunNotFoundError(SchedulerError):
    """Raised when a run is not found or does not belong to the given schedule."""


class ScheduleRunError(SchedulerError):
    """Raised when a scheduled run fails; carries schedule_id and run_id for correlation.

    Attributes:
        status_code: HTTP status code returned by the target, when known. Used by the
            retry orchestrator to decide whether a failed attempt is retryable. ``None``
            for transport-level failures and timeouts, which carry no status code.
    """

    def __init__(self, message: str, schedule_id: str, run_id: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.schedule_id = schedule_id
        self.run_id = run_id
        self.status_code = status_code


class DialServiceError(SchedulerError):
    """Raised when the DIAL service is unreachable or returns an unexpected non-2xx response."""
