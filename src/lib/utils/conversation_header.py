from urllib.parse import quote

from src.lib.constants import CONVERSATION_ID_HEADER


# Every printable ASCII character (0x20-0x7E) passes through; anything else is percent-encoded.
_PRINTABLE_ASCII = "".join(chr(code) for code in range(0x20, 0x7F))


def encode_header_value(value: str) -> str:
    """Make a value safe to send as an HTTP header value.

    httpx encodes header values as ASCII, so a non-ASCII character (e.g. a Cyrillic schedule name)
    would raise and fail the run, and control characters are rejected as invalid header bytes. Each
    character outside printable ASCII is therefore replaced by the percent-encoding of its UTF-8
    bytes. Printable ASCII, including space, ``/`` and ``%``, is kept: conversation name parts
    already escape ``%``, ``/`` and ``\\``, so a literal ``%`` is unambiguous and an ASCII
    conversation id is sent byte for byte as stored.

    Args:
        value: The raw header value.

    Returns:
        A printable-ASCII value; unchanged when ``value`` is already printable ASCII.
    """
    return quote(value, safe=_PRINTABLE_ASCII)


def with_conversation_id(headers: dict[str, str], conversation_id: str | None) -> dict[str, str]:
    """Return request headers carrying the scheduler's ``X-CONVERSATION-ID``.

    The scheduler's value takes precedence over a caller-supplied one. Header names are
    case-insensitive and httpx keeps differently-cased duplicates as separate values, so every
    matching key is removed regardless of case before the scheduler's value is added.

    Args:
        headers: Request headers, typically a schedule's ``extra_headers``.
        conversation_id: The run's conversation id, or ``None`` when the scheduler sends none.

    Returns:
        A copy of ``headers``; unchanged when ``conversation_id`` is ``None``.
    """
    if conversation_id is None:
        return dict(headers)
    result = {name: value for name, value in headers.items() if name.lower() != CONVERSATION_ID_HEADER.lower()}
    result[CONVERSATION_ID_HEADER] = encode_header_value(conversation_id)
    return result
