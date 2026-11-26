from functools import lru_cache
from pathlib import Path
import tomllib

from src.lib.constants import USER_AGENT_HEADER, USER_AGENT_PRODUCT
from src.lib.utils.headers import without_headers
from src.lib.utils.logging import logger


# The project is not installed as a package (``tool.uv.package = false``), so importlib.metadata
# cannot resolve the version; pyproject.toml ships next to ``src/`` in the image and stays the
# single source of truth for it.
PYPROJECT_PATH = Path(__file__).resolve().parents[3] / "pyproject.toml"


@lru_cache(maxsize=1)
def get_user_agent(pyproject_path: Path = PYPROJECT_PATH) -> str:
    """Build the ``User-Agent`` value identifying the scheduler on outbound requests.

    Args:
        pyproject_path: Location of the project's pyproject.toml holding the version.

    Returns:
        ``ai-dial-scheduler/<version>``, or the bare product name when the version is unreadable.
    """
    try:
        with pyproject_path.open("rb") as file:
            version = tomllib.load(file)["project"]["version"]
    except (OSError, tomllib.TOMLDecodeError, KeyError, TypeError) as exc:
        logger.warning("user_agent_version_unavailable", path=str(pyproject_path), error=str(exc))
        return USER_AGENT_PRODUCT
    return f"{USER_AGENT_PRODUCT}/{version}"


def strip_user_agent(headers: dict[str, str]) -> dict[str, str]:
    """Drop any caller-supplied ``User-Agent`` so the scheduler's own value always wins.

    Header names are case-insensitive, and httpx keeps differently-cased duplicates as separate
    values, so the match ignores case.

    Args:
        headers: Request headers, typically a schedule's ``extra_headers``.

    Returns:
        A copy of ``headers`` without any ``User-Agent`` entry.
    """
    return without_headers(headers, (USER_AGENT_HEADER,))
