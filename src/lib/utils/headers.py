from collections.abc import Iterable


def without_headers(headers: dict[str, str], names: Iterable[str]) -> dict[str, str]:
    """Return a copy of ``headers`` without any entry whose name matches one of ``names``.

    Header names are case-insensitive, and httpx (and the OpenAI SDK, which merges header dicts
    before handing them to httpx) keeps differently-cased duplicates as separate header lines, so
    the match ignores case. Used to keep caller-supplied ``extra_headers`` from duplicating headers
    the scheduler owns.

    Args:
        headers: Request headers, typically a schedule's ``extra_headers``.
        names: Header names to drop.

    Returns:
        A new dict; ``headers`` is not modified.
    """
    blocked = {name.lower() for name in names}
    return {name: value for name, value in headers.items() if name.lower() not in blocked}
