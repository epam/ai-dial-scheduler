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
    """Encapsulates pagination parameters and builds a Page from a result set."""

    limit: int
    offset: int
    base_path: str

    def paginate(self, count: int, results: list[_S]) -> Page[_S]:
        next_offset = self.offset + self.limit
        next_link = f"{self.base_path}?limit={self.limit}&offset={next_offset}" if next_offset < count else None
        previous_link = (
            f"{self.base_path}?limit={self.limit}&offset={max(self.offset - self.limit, 0)}"
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
