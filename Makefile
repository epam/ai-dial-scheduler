.PHONY: install lint test

install:
	uv sync --all-extras

lint: install
	uv run ruff check src tests
	uv run ruff format --check src tests
	uv run mypy src

test: install
	uv run pytest
