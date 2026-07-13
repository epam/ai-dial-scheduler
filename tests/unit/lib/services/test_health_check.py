"""Unit tests for the shared check_database health-check helper."""

import asyncio

import pytest
from sqlalchemy.exc import OperationalError

from src.lib.services.health_check import check_database


@pytest.fixture()
def patched_sessionmaker(mocker):
    def _patch(session_ctx):
        mock_sessionmaker = mocker.MagicMock(return_value=session_ctx)
        mocker.patch("src.lib.services.health_check.Database").return_value.sessionmaker = mock_sessionmaker

    return _patch


class TestCheckDatabaseSuccess:
    async def test_returns_true_when_db_reachable(self, mocker, patched_sessionmaker):
        mock_session = mocker.AsyncMock()
        mock_session.execute = mocker.AsyncMock(return_value=mocker.MagicMock())

        mock_ctx = mocker.AsyncMock()
        mock_ctx.__aenter__ = mocker.AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = mocker.AsyncMock(return_value=False)
        patched_sessionmaker(mock_ctx)

        result = await check_database(2.0)

        assert result is True


class TestCheckDatabaseFailure:
    async def test_returns_false_on_operational_error(self, mocker, patched_sessionmaker):
        mock_ctx = mocker.AsyncMock()
        mock_ctx.__aenter__ = mocker.AsyncMock(side_effect=OperationalError("connection refused", None, None))
        mock_ctx.__aexit__ = mocker.AsyncMock(return_value=False)
        patched_sessionmaker(mock_ctx)

        result = await check_database(2.0)

        assert result is False

    async def test_returns_false_on_timeout(self, mocker, patched_sessionmaker):
        async def slow_execute(*_args, **_kwargs):
            await asyncio.sleep(10)

        mock_session = mocker.AsyncMock()
        mock_session.execute.side_effect = slow_execute

        mock_ctx = mocker.AsyncMock()
        mock_ctx.__aenter__ = mocker.AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = mocker.AsyncMock(return_value=False)
        patched_sessionmaker(mock_ctx)

        result = await check_database(0.01)

        assert result is False

    async def test_returns_false_on_generic_exception(self, mocker, patched_sessionmaker):
        mock_ctx = mocker.AsyncMock()
        mock_ctx.__aenter__ = mocker.AsyncMock(side_effect=Exception("connection error"))
        mock_ctx.__aexit__ = mocker.AsyncMock(return_value=False)
        patched_sessionmaker(mock_ctx)

        result = await check_database(2.0)

        assert result is False

    async def test_logs_warning_on_failure(self, mocker, patched_sessionmaker):
        mock_ctx = mocker.AsyncMock()
        mock_ctx.__aenter__ = mocker.AsyncMock(side_effect=Exception("connection error"))
        mock_ctx.__aexit__ = mocker.AsyncMock(return_value=False)
        patched_sessionmaker(mock_ctx)
        mock_logger = mocker.patch("src.lib.services.health_check.logger")

        await check_database(2.0)

        mock_logger.warning.assert_called_once()
