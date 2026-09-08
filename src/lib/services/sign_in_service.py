from src.lib.clients.dial_client import DialClient
from src.lib.constants import EXTERNAL_SERVICE_URL_TEMPLATE
from src.lib.exceptions.errors import DialServiceError, SignInRequiredError
from src.lib.schemas.sign_in import SignInStatus
from src.lib.utils.logging import logger


class SignInService:
    """Checks whether a user is signed in to a DIAL external service.

    Probes ``GET /v1/applications/{app_id}`` (:meth:`DialClient.get_external_service_auth_status`)
    authenticated with the caller's own ``Api-Key``: the returned statuses reflect that same
    identity, so this only answers "is the caller signed in", never an arbitrary other user's
    status. Any failure to reach DIAL or parse the response leaves the sign-in state undetermined
    rather than being reported as "not signed in".
    """

    def __init__(
        self,
        dial_client: DialClient,
        scheduler_app_id: str,
        signin_timeout_seconds: float,
    ) -> None:
        self._dial_client = dial_client
        self._scheduler_app_id = scheduler_app_id
        self._signin_timeout_seconds = signin_timeout_seconds

    async def check(
        self,
        user_id: str,
        service_id: str,
        caller_api_key: str,
        channel_id: str | None = None,
    ) -> SignInStatus:
        """Return the current sign-in state for ``user_id`` against ``service_id``.

        Never raises: any failure to determine the state is reported as ``state="unknown"``.
        If ``channel_id`` is given and the user is not signed in, pushes an
        external-service/signin popup to that chat session and, on success, re-checks. The
        interact call is made as the caller (``caller_api_key``), not as the scheduler's own
        service identity: DIAL Core resolves the client channel by the caller's own bucket,
        which must match the chat session that subscribed to it.
        """
        status = await self._probe(user_id, service_id, caller_api_key)
        if status.state != "signed_in" and channel_id:
            scope = EXTERNAL_SERVICE_URL_TEMPLATE.format(app_id=self._scheduler_app_id, service_id=service_id)
            signed_in_now = await self._dial_client.request_external_service_signin(
                caller_api_key, channel_id, scope, self._signin_timeout_seconds
            )
            if signed_in_now:
                status = await self._probe(user_id, service_id, caller_api_key)
        return status

    async def _probe(self, user_id: str, service_id: str, caller_api_key: str) -> SignInStatus:
        logger.info("sign_in_check_started", user_id=user_id, service_id=service_id)
        try:
            auth_status = await self._dial_client.get_external_service_auth_status(
                caller_api_key, self._scheduler_app_id, service_id
            )
        except DialServiceError as exc:
            logger.warning("sign_in_check_failed", user_id=user_id, service_id=service_id, reason=str(exc))
            status = SignInStatus(
                service_id=service_id,
                state="unknown",
                detail=(
                    f"Could not determine sign-in status for {service_id}. "
                    "This is not necessarily a missing sign-in; verify the service id and retry."
                ),
            )
        else:
            if auth_status.app_level_auth_status != "SIGNED_IN":
                status = SignInStatus(
                    service_id=service_id,
                    state="unknown",
                    detail=(
                        f"{service_id} has not been approved for use by an administrator. "
                        "Signing in will not resolve this; contact an administrator."
                    ),
                )
            elif auth_status.user_level_auth_status == "SIGNED_IN":
                status = SignInStatus(service_id=service_id, state="signed_in", detail=f"Signed in to {service_id}.")
            else:
                status = SignInStatus(
                    service_id=service_id,
                    state="not_signed_in",
                    detail=(
                        f"You are not signed in to {service_id}. Open the DIAL Scheduler "
                        "application in DIAL Chat and sign in to it, then retry this request."
                    ),
                )

        logger.info("sign_in_check_result", user_id=user_id, service_id=service_id, state=status.state)
        return status

    async def require(
        self,
        user_id: str,
        service_id: str,
        caller_api_key: str,
        channel_id: str | None = None,
    ) -> None:
        """Raise :class:`SignInRequiredError` unless the user is signed in to ``service_id``."""
        status = await self.check(user_id, service_id, caller_api_key, channel_id)
        if status.state != "signed_in":
            raise SignInRequiredError(status)
