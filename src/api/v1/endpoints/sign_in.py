from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query

from src.api.dependencies.auth import get_user_id
from src.api.dependencies.sign_in import get_sign_in_service
from src.lib.constants import API_KEY_HEADER, CLIENT_CHANNEL_HEADER
from src.lib.schemas.sign_in import SignInStatus
from src.lib.services.sign_in_service import SignInService


router = APIRouter(prefix="/sign-in", tags=["sign-in"])

SignInServiceDep = Annotated[SignInService, Depends(get_sign_in_service)]
ChannelIdDep = Annotated[str | None, Header(alias=CLIENT_CHANNEL_HEADER)]


@router.get("/status", response_model=SignInStatus, operation_id="get_sign_in_status")
async def get_sign_in_status(
    service_id: Annotated[str, Query(min_length=1, pattern=r"\S")],
    sign_in_service: SignInServiceDep,
    user_id: Annotated[str, Depends(get_user_id)],
    api_key: Annotated[str, Header(alias=API_KEY_HEADER)],
    channel_id: ChannelIdDep = None,
) -> SignInStatus:
    """Report whether the caller is signed in to a DIAL external service.

    Read-only: changes no scheduler or DIAL state. `state` is the field to branch on
    (`signed_in`, `not_signed_in`, or `unknown` when DIAL could not be reached to determine
    the answer). `detail` is a human-readable reason safe to relay verbatim to the user.
    """
    return await sign_in_service.check(user_id, service_id, api_key, channel_id)
