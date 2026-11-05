from fastapi import Request


METADATA_FILTER_PREFIX = "metadata__"


def parse_metadata_filter(request: Request) -> dict[str, str]:
    """Extract ``metadata__<key>=<value>`` query parameters into a filter map.

    The key is the substring after the first ``metadata__`` prefix (so keys may
    themselves contain ``__``). Parameters without the prefix are ignored, and an
    empty key (a bare ``metadata__``) is skipped. When a key appears more than once,
    the last value wins.

    Args:
        request: The incoming HTTP request.

    Returns:
        A mapping of metadata key to the expected string value; empty when no
        metadata filters are present.
    """
    result: dict[str, str] = {}
    for key, value in request.query_params.multi_items():
        if not key.startswith(METADATA_FILTER_PREFIX):
            continue
        meta_key = key[len(METADATA_FILTER_PREFIX) :]
        if not meta_key:
            continue
        result[meta_key] = value
    return result
