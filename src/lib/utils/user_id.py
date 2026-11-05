from typing import Any


def extract_user_id(data: dict[str, Any], paths: list[str]) -> str | None:
    """Return the first non-empty user id found by walking the ordered candidate path list.

    Each path is dot-separated. Pure-digit segments are treated as list indices;
    all other segments are dict keys. Returns None when no path yields a non-empty value.
    """
    for path in paths:
        segments = path.split(".")
        current: Any = data
        for segment in segments:
            if segment.isdigit():
                if not isinstance(current, list):
                    current = None
                    break
                index = int(segment)
                if index >= len(current):
                    current = None
                    break
                current = current[index]
            else:
                if not isinstance(current, dict):
                    current = None
                    break
                if segment not in current:
                    current = None
                    break
                current = current[segment]
        if current is None:
            continue
        value = str(current).strip()
        if value:
            return value
    return None
