from dataclasses import dataclass
from typing import Generic, TypeVar

from pydantic import BaseModel


T = TypeVar("T")
_S = TypeVar("_S")


class Page(BaseModel, Generic[T]):
    """Generic paginated response envelope."""

    count: int
    limit: int
    offset: int
    results: list[T]
    next: str | None
    previous: str | None


@dataclass
class Paginator:
    """Encapsulates pagination parameters and builds a Page from a result set.

    ``extra_query`` is an optional, already-formatted query string (without a leading
    ``&``) appended to the generated ``next``/``previous`` links so callers can preserve
    resource-specific parameters (e.g. ordering) across pages. It is kept
    resource-agnostic: the paginator neither parses nor validates its contents.
    """

    limit: int
    offset: int
    base_path: str
    extra_query: str = ""

    def paginate(self, count: int, results: list[_S]) -> Page[_S]:
        suffix = f"&{self.extra_query}" if self.extra_query else ""
        next_offset = self.offset + self.limit
        next_link = f"{self.base_path}?limit={self.limit}&offset={next_offset}{suffix}" if next_offset < count else None
        previous_link = (
            f"{self.base_path}?limit={self.limit}&offset={max(self.offset - self.limit, 0)}{suffix}"
            if self.offset > 0
            else None
        )
        return Page(
            count=count,
            limit=self.limit,
            offset=self.offset,
            results=results,
            next=next_link,
            previous=previous_link,
        )
