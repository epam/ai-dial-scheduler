from src.lib.schemas.pagination import Page, Paginator


class TestPaginatorNextLink:
    def test_returns_none_on_last_page(self):
        p = Paginator(limit=10, offset=0, base_path="/items")
        assert p.paginate(count=10, results=[]).next is None

    def test_returns_none_when_offset_plus_limit_equals_total(self):
        p = Paginator(limit=5, offset=5, base_path="/items")
        assert p.paginate(count=10, results=[]).next is None

    def test_returns_link_when_more_results_exist(self):
        p = Paginator(limit=5, offset=0, base_path="/items")
        assert p.paginate(count=10, results=[]).next == "/items?limit=5&offset=5"

    def test_advances_offset_by_limit(self):
        p = Paginator(limit=3, offset=6, base_path="/items")
        assert p.paginate(count=20, results=[]).next == "/items?limit=3&offset=9"


class TestPaginatorPreviousLink:
    def test_returns_none_on_first_page(self):
        p = Paginator(limit=10, offset=0, base_path="/items")
        assert p.paginate(count=10, results=[]).previous is None

    def test_returns_link_when_offset_positive(self):
        p = Paginator(limit=5, offset=5, base_path="/items")
        assert p.paginate(count=10, results=[]).previous == "/items?limit=5&offset=0"

    def test_clamps_previous_offset_to_zero(self):
        p = Paginator(limit=10, offset=3, base_path="/items")
        assert p.paginate(count=10, results=[]).previous == "/items?limit=10&offset=0"

    def test_advances_offset_by_limit(self):
        p = Paginator(limit=5, offset=10, base_path="/items")
        assert p.paginate(count=20, results=[]).previous == "/items?limit=5&offset=5"


class TestPaginatorPaginate:
    def test_returns_page_with_correct_shape(self):
        p = Paginator(limit=2, offset=0, base_path="/items")
        result = p.paginate(count=5, results=["a", "b"])

        assert isinstance(result, Page)
        assert result.count == 5
        assert result.limit == 2
        assert result.offset == 0
        assert result.results == ["a", "b"]


class TestPaginatorExtraQuery:
    def test_next_link_appends_extra_query(self):
        p = Paginator(limit=5, offset=0, base_path="/items", extra_query="order_by=created_at&order_dir=asc")
        assert p.paginate(count=10, results=[]).next == "/items?limit=5&offset=5&order_by=created_at&order_dir=asc"

    def test_previous_link_appends_extra_query(self):
        p = Paginator(limit=5, offset=5, base_path="/items", extra_query="order_by=name&order_dir=desc")
        assert p.paginate(count=10, results=[]).previous == "/items?limit=5&offset=0&order_by=name&order_dir=desc"

    def test_no_extra_query_leaves_links_unchanged(self):
        p = Paginator(limit=5, offset=0, base_path="/items")
        assert p.paginate(count=10, results=[]).next == "/items?limit=5&offset=5"

    def test_empty_extra_query_leaves_links_unchanged(self):
        p = Paginator(limit=5, offset=0, base_path="/items", extra_query="")
        assert p.paginate(count=10, results=[]).next == "/items?limit=5&offset=5"
