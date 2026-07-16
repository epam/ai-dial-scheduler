"""Automate the local DIAL external-service sign-in flow (see README.md, "Per-User Credentials").

Drives the full flow against a local `dial/docker-compose.yml` stack:
1. Issue a Keycloak user access token for the test user (password grant).
2. Drive the Keycloak login form to obtain an OAuth2 authorization code for the
   scheduler's `dial-oauth` external service (no browser needed).
3. Call DIAL core's external-service sign-in endpoint with that token and code.

Example:
    uv run python scripts/dial_sign_in.py
    uv run python scripts/dial_sign_in.py --dial-url http://localhost:8081
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import re
from urllib.parse import parse_qs, urlparse
import uuid

import httpx


logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

KEYCLOAK_URL = "http://localhost:8900"
REALM = "dial"
CLIENT_ID = "dial-chat"
CLIENT_SECRET = "keycloak_secret"
USERNAME = "dial"
PASSWORD = "dial"

DIAL_URL = "http://localhost:8081"
REDIRECT_URI = "http://localhost:3100/callback"
EXTERNAL_SERVICE_URL = "applications/dial_scheduler/external_services/dial-oauth"


def _decode_sub(token: str) -> str | None:
    """Extract the `sub` claim from a JWT without verifying its signature.

    Args:
        token: A JWT access token.

    Returns:
        The token's `sub` claim, or None if it cannot be decoded.
    """
    try:
        payload_segment = token.split(".")[1]
        padding = "=" * (-len(payload_segment) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_segment + padding))
    except IndexError, ValueError:
        return None
    return payload.get("sub")


async def issue_token(client: httpx.AsyncClient) -> str:
    """Issue a Keycloak user access token via the Resource Owner Password Credentials grant.

    Args:
        client: HTTP client used to reach Keycloak.

    Returns:
        The bearer access token for the DIAL test user.
    """
    response = await client.post(
        f"{KEYCLOAK_URL}/realms/{REALM}/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "username": USERNAME,
            "password": PASSWORD,
        },
    )
    response.raise_for_status()
    return response.json()["access_token"]


async def _issue_authorization_code(client: httpx.AsyncClient) -> str:
    """Drive the Keycloak login form to obtain an OAuth2 authorization code.

    Args:
        client: HTTP client that keeps cookies across the login redirect chain.

    Returns:
        The authorization code issued by Keycloak for the `dial-chat` client.
    """
    auth_response = await client.get(
        f"{KEYCLOAK_URL}/realms/{REALM}/protocol/openid-connect/auth",
        params={
            "response_type": "code",
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "scope": "openid offline_access",
            "state": uuid.uuid4().hex,
        },
    )
    auth_response.raise_for_status()

    form_action_match = re.search(r'<form[^>]+action="([^"]+)"', auth_response.text)
    if not form_action_match:
        raise RuntimeError("Could not locate the Keycloak login form action")
    login_url = form_action_match.group(1).replace("&amp;", "&")

    # Keycloak marks its login-session cookies `Secure`, so httpx's cookie jar
    # refuses to replay them over the plain-http localhost stack. Relay them
    # manually instead of relying on the client's automatic cookie handling.
    session_cookies = "; ".join(f"{name}={value}" for name, value in auth_response.cookies.items())

    login_response = await client.post(
        login_url,
        data={"username": USERNAME, "password": PASSWORD},
        headers={"Cookie": session_cookies},
        follow_redirects=False,
    )
    redirect_location = login_response.headers.get("location")
    if login_response.status_code not in (302, 303) or not redirect_location:
        raise RuntimeError("Keycloak login did not redirect to the callback; check credentials")

    code = parse_qs(urlparse(redirect_location).query).get("code", [None])[0]
    if not code:
        raise RuntimeError(f"No authorization code found in redirect: {redirect_location}")
    return code


async def sign_in(client: httpx.AsyncClient, user_token: str) -> dict:
    """Sign the DIAL user in to the scheduler's `dial-oauth` external service.

    Obtains a fresh authorization code and exchanges it, together with the
    user's bearer token, for OBO credentials via DIAL core.

    Args:
        client: HTTP client used for both the Keycloak login and the sign-in call.
        user_token: The bearer token returned by `issue_token`.

    Returns:
        The JSON response body from the DIAL core sign-in call.
    """
    code = await _issue_authorization_code(client)

    response = await client.post(
        f"{DIAL_URL}/v1/ops/external-service/signin",
        headers={"Authorization": f"Bearer {user_token}"},
        json={
            "url": EXTERNAL_SERVICE_URL,
            "credentials_level": "USER",
            "authentication_type": "OAUTH",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "offline_usage_consent": True,
        },
    )
    response.raise_for_status()
    return response.json()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keycloak-url", default=KEYCLOAK_URL)
    parser.add_argument("--dial-url", default=DIAL_URL)
    parser.add_argument("--username", default=USERNAME)
    parser.add_argument("--password", default=PASSWORD)
    return parser.parse_args()


async def _main() -> None:
    args = _parse_args()
    global KEYCLOAK_URL, DIAL_URL, USERNAME, PASSWORD  # noqa: PLW0603
    KEYCLOAK_URL = args.keycloak_url
    DIAL_URL = args.dial_url
    USERNAME = args.username
    PASSWORD = args.password

    async with httpx.AsyncClient() as client:
        token = await issue_token(client)
        logger.info("Issued user token (sub=%s): %s", _decode_sub(token), token)
        result = await sign_in(client, token)
        logger.info("Sign-in result: %s", result)


if __name__ == "__main__":
    asyncio.run(_main())
