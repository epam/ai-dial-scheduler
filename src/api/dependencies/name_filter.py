from fastapi import Request


NAME_FILTER_PARAM = "name"


def parse_name_filter(request: Request) -> str | None:
    """Extract the ``name`` query parameter as a case-insensitive substring filter term.

    Surrounding whitespace is stripped; internal whitespace is preserved. A missing,
    empty, or whitespace-only value yields ``None`` (no filtering). When the parameter
    appears more than once, the last value wins.

    Args:
        request: The incoming HTTP request.

    Returns:
        The trimmed filter term, or ``None`` when no usable term is present.
    """
    value: str | None = None
    for key, raw in request.query_params.multi_items():
        if key == NAME_FILTER_PARAM:
            value = raw
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None
