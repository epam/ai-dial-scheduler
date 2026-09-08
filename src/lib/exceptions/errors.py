from typing import TYPE_CHECKING, Literal, cast


if TYPE_CHECKING:
    from src.lib.schemas.sign_in import SignInStatus


class SchedulerError(Exception):
    """Base for all scheduler domain exceptions."""


class ScheduleNotFoundError(SchedulerError):
    """Raised when a schedule is not found or not owned by the caller."""


class ScheduleAlreadyDeletedError(SchedulerError):
    """Raised when an operation targets a schedule that is already soft-deleted.

    Distinct from :class:`ScheduleNotFoundError`: the schedule exists and is still readable,
    but it is frozen — it cannot be updated, paused, resumed, deleted again, or restored.
    Surfaced as HTTP 409, whereas a missing or unowned schedule is a 404.
    """


class JobRegistrationError(SchedulerError):
    """Raised when APScheduler fails to register a job after row persistence."""


class JobRemovalError(SchedulerError):
    """Raised when APScheduler fails to unregister a job during schedule deletion.

    The job is unregistered before any database write, so this leaves the schedule fully
    intact and still live: the caller receives HTTP 502 and may safely retry. A missing job
    is *not* an error — it is treated as already unregistered.
    """


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


class SignInRequiredError(SchedulerError):
    """Raised when a user is not signed in (or sign-in status is undetermined) for a service.

    Wraps a :class:`~src.lib.schemas.sign_in.SignInStatus` whose ``state`` is never
    ``"signed_in"``, so the raising site (e.g. a schedule create/update precondition) can build
    a structured HTTP error body without parsing the exception message text.
    """

    def __init__(self, status: "SignInStatus") -> None:
        if status.state == "signed_in":
            raise ValueError("SignInRequiredError requires a status whose state is not 'signed_in'")
        super().__init__(status.detail)
        self.status = status

    @property
    def service_id(self) -> str:
        return self.status.service_id

    @property
    def state(self) -> Literal["not_signed_in", "unknown"]:
        # __init__ rejects a "signed_in" status above, so this narrowing reflects an invariant
        # enforced at construction time rather than an unchecked assumption.
        return cast(Literal["not_signed_in", "unknown"], self.status.state)

    @property
    def detail(self) -> str:
        return self.status.detail
