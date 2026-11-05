from dataclasses import dataclass

from fastapi import Query

from src.lib.constants import DEFAULT_PAGE_LIMIT
from src.lib.schemas.pagination import Paginator


@dataclass
class Pagination:
    limit: int = Query(default=DEFAULT_PAGE_LIMIT, ge=1)
    offset: int = Query(default=0, ge=0)

    def paginator(self, base_path: str) -> Paginator:
        return Paginator(limit=self.limit, offset=self.offset, base_path=base_path)
