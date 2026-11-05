"""Connect offline credentials for the local DIAL stack (see README.md, "Per-User Credentials").

Drives the two decisions that DIAL_NATIVE offline delegation requires, against a local
`dial/docker-compose.yml` stack:

1. An admin approves the scheduler application for the external service (layer 2).
2. The user allows DIAL to act on their behalf while they are away (layer 1) — an
   authorization-code flow against the offline client, followed by DIAL core's
   offline-credentials sign-in.

Neither works alone: consent with no credentials has no identity to act with, and
credentials with no consent reach no application.

Example:
    uv run python scripts/dial_sign_in.py
    uv run python scripts/dial_sign_in.py --username user --password dial --skip-consent
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
APP_ID = "public/dial_scheduler__1.0.0"
SERVICE_ID = "dial-native"


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


async def get_status(client: httpx.AsyncClient, user_token: str) -> dict:
    """Read the caller's offline-credentials status and connect parameters.

    Args:
        client: HTTP client used to reach DIAL core.
        user_token: The bearer token returned by `issue_token`.

    Returns:
        The status body: `connected`, `available`, and a `connect` block when there is
        something to do.
    """
    response = await client.get(
        f"{DIAL_URL}/v1/user/offline-credentials",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    response.raise_for_status()
    return response.json()


async def grant_consent(client: httpx.AsyncClient, admin_token: str) -> None:
    """Approve the scheduler application for the external service, as an administrator.

    Approval is installation-wide: it lets the application act as any user who has
    connected offline credentials, not only the caller.

    Args:
        client: HTTP client used to reach DIAL core.
        admin_token: A bearer token for a user holding the DIAL admin role.
    """
    response = await client.post(
        f"{DIAL_URL}/v1/applications/{APP_ID}/external-services/{SERVICE_ID}/consent",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    response.raise_for_status()


async def _issue_authorization_code(client: httpx.AsyncClient, connect: dict) -> str:
    """Drive the Keycloak login form to obtain an OAuth2 authorization code.

    Args:
        client: HTTP client that keeps cookies across the login redirect chain.
        connect: The `connect` block published by DIAL core, naming the authorization
            endpoint, client id, redirect URI and scopes to use.

    Returns:
        The authorization code issued by Keycloak for the offline client.
    """
    auth_response = await client.get(
        connect["authorization_endpoint"],
        params={
            "response_type": "code",
            "client_id": connect["client_id"],
            "redirect_uri": connect["redirect_uri"],
            "scope": " ".join(connect["scopes"]),
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


async def connect_offline_credentials(client: httpx.AsyncClient, user_token: str, connect: dict) -> None:
    """Connect the user's offline credentials from a freshly issued authorization code.

    Args:
        client: HTTP client used for both the Keycloak login and the sign-in call.
        user_token: The bearer token returned by `issue_token`.
        connect: The `connect` block published by DIAL core.
    """
    code = await _issue_authorization_code(client, connect)

    response = await client.post(
        f"{DIAL_URL}/v1/user/offline-credentials/signin",
        headers={"Authorization": f"Bearer {user_token}"},
        json={"code": code, "redirect_uri": connect["redirect_uri"]},
    )
    response.raise_for_status()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keycloak-url", default=KEYCLOAK_URL)
    parser.add_argument("--dial-url", default=DIAL_URL)
    parser.add_argument("--username", default=USERNAME)
    parser.add_argument("--password", default=PASSWORD)
    parser.add_argument("--app-id", default=APP_ID)
    parser.add_argument("--service-id", default=SERVICE_ID)
    parser.add_argument(
        "--skip-consent",
        action="store_true",
        help="Do not grant admin consent (use when the signing-in user is not an administrator)",
    )
    return parser.parse_args()


async def _main() -> None:
    args = _parse_args()
    global KEYCLOAK_URL, DIAL_URL, USERNAME, PASSWORD, APP_ID, SERVICE_ID  # noqa: PLW0603
    KEYCLOAK_URL = args.keycloak_url
    DIAL_URL = args.dial_url
    USERNAME = args.username
    PASSWORD = args.password
    APP_ID = args.app_id
    SERVICE_ID = args.service_id

    async with httpx.AsyncClient() as client:
        token = await issue_token(client)
        logger.info("Issued user token for %s (sub=%s)", USERNAME, _decode_sub(token))

        if not args.skip_consent:
            await grant_consent(client, token)
            logger.info("Granted admin consent for %s / %s", APP_ID, SERVICE_ID)

        status = await get_status(client, token)
        if not status.get("available"):
            raise RuntimeError(
                "Core publishes no offline client for this user's identity provider; "
                "check the aidial.identityProviders.*.offlineClient settings"
            )
        if status.get("connected"):
            logger.info("Offline credentials already connected; nothing to do")
            return

        await connect_offline_credentials(client, token, status["connect"])
        logger.info("Connected offline credentials: %s", await get_status(client, token))


if __name__ == "__main__":
    asyncio.run(_main())
