from typing import Annotated

from fastapi import Depends

from src.api.dependencies.auth import get_dial_client, get_settings_dep
from src.lib.clients.dial_client import DialClient
from src.lib.services.sign_in_service import SignInService
from src.lib.settings import Settings


def get_sign_in_service(
    dial_client: Annotated[DialClient, Depends(get_dial_client)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> SignInService:
    return SignInService(
        dial_client,
        scheduler_app_id=settings.scheduler_app_id,
        signin_timeout_seconds=settings.client_channel_signin_timeout_seconds,
    )
