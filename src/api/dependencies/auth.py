from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request
import httpx

from src.lib.clients.dial_client import DialClient
from src.lib.constants import API_KEY_HEADER
from src.lib.exceptions.errors import DialServiceError
from src.lib.settings import Settings, get_settings
from src.lib.utils.logging import logger
from src.lib.utils.user_id import extract_user_id


def get_settings_dep() -> Settings:
    return get_settings()


def get_dial_client(request: Request, settings: Annotated[Settings, Depends(get_settings_dep)]) -> DialClient:
    http_client: httpx.AsyncClient = request.app.state.app_client
    return DialClient(http_client=http_client, base_url=settings.dial_url, timeout=settings.http_timeout_seconds)


async def get_user_id(
    api_key: Annotated[str, Header(alias=API_KEY_HEADER)],
    dial_client: Annotated[DialClient, Depends(get_dial_client)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> str:
    try:
        info = await dial_client.get_user_info(api_key)
    except DialServiceError:
        logger.info("auth_failed", reason="dial_unavailable")
        raise HTTPException(status_code=502, detail="DIAL service unavailable")

    user_id = extract_user_id(info, settings.user_id_path_list)
    if user_id is None:
        logger.info("auth_rejected", reason="no_identity")
        raise HTTPException(status_code=401, detail="Could not resolve user identity from DIAL response")

    logger.info("auth_success")
    return user_id
