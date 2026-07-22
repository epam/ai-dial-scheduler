_BEARER_PREFIX = "Bearer "


def strip_bearer_prefix(token: str) -> str:
    """Return the raw token, removing a leading ``Bearer `` prefix if present.

    OAUTH-type external services return their credential as ``Bearer <jwt>``; DIAL's
    ``Api-Key`` header expects only the raw token value.
    """
    if token.startswith(_BEARER_PREFIX):
        return token[len(_BEARER_PREFIX) :]
    return token
