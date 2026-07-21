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


class ConversationCreationError(SchedulerError):
    """Raised when a DIAL conversation cannot be created after a successful run.

    This is a worker-internal, non-fatal signal: it is always caught at the job boundary,
    logged for diagnostics, and never re-raised past a successful run's outcome (FR-013).
    It never carries issued credentials or streamed content.
    """


class OboCredentialsError(SchedulerError):
    """Raised when on-behalf-of credentials cannot be obtained for a schedule's owner.

    Carries the DIAL response (status code and body) so the worker can persist a
    diagnosable, credentials-stage run error without leaking any issued secret.

    Attributes:
        status_code: The DIAL HTTP status code, when the failure was an HTTP response.
            ``None`` for transport-level failures.
        response_body: The DIAL response body (parsed JSON or raw text) describing the
            failure. Never contains issued credentials.
    """

    def __init__(self, message: str, status_code: int | None = None, response_body: object = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body
