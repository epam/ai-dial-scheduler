from urllib.parse import urlsplit


_BEARER_PREFIX = "Bearer "


def strip_bearer_prefix(token: str) -> str:
    """Return the raw token, removing a leading ``Bearer `` prefix if present.

    OAUTH-type external services return their credential as ``Bearer <jwt>``; DIAL's
    ``Api-Key`` header expects only the raw token value.
    """
    if token.startswith(_BEARER_PREFIX):
        return token[len(_BEARER_PREFIX) :]
    return token


def parse_deployment(url: str) -> tuple[str, str]:
    """Return ``(azure_endpoint, azure_deployment)`` parsed from a deployment URL.

    The endpoint is the scheme and network location; the deployment id is resolved via
    :func:`extract_deployment_id`, so multi-segment application deployment ids
    (e.g. ``applications/{bucket}/{name}``) are preserved consistently for all executors.

    Raises:
        ValueError: When the URL path does not contain a ``deployments/<id>`` segment.
    """
    parsed = urlsplit(url)
    endpoint = f"{parsed.scheme}://{parsed.netloc}"
    return endpoint, extract_deployment_id(url)


def extract_deployment_id(url: str) -> str:
    """Return the deployment id from a chat completion URL.

    The deployment id is the path segment(s) between ``/deployments/`` and the trailing
    ``/chat/completions``. Application deployment ids themselves contain ``/`` (e.g.
    ``applications/{bucket}/{name}``), so the full multi-segment id is preserved.

    Raises:
        ValueError: When the URL path does not contain a deployment segment.
    """
    path = urlsplit(url).path
    marker = "/deployments/"
    start = path.find(marker)
    if start == -1:
        raise ValueError(f"cannot extract deployment from URL path: {path}")
    remainder = path[start + len(marker) :]
    suffix = "/chat/completions"
    if remainder.endswith(suffix):
        remainder = remainder[: -len(suffix)]
    deployment = remainder.strip("/")
    if not deployment:
        raise ValueError(f"cannot extract deployment from URL path: {path}")
    return deployment
