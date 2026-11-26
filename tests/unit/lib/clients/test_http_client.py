import httpx
from pydantic import SecretStr

from src.lib.clients.dial_client import DialClient
from src.lib.clients.http_client import create_http_client
from src.lib.constants import CONVERSATION_ID_HEADER
from src.lib.schemas.credentials import OboCredentials
from src.lib.settings import ApiHttpSettings, WorkerHttpSettings
from src.lib.utils.user_agent import get_user_agent


class TestCreateHttpClient:
    async def test_sets_scheduler_user_agent(self):
        async with create_http_client(WorkerHttpSettings()) as client:
            assert client.headers["User-Agent"] == get_user_agent()

    async def test_applies_timeouts_from_settings(self):
        config = ApiHttpSettings(
            connect_timeout_seconds=1.0,
            read_timeout_seconds=2.0,
            write_timeout_seconds=3.0,
            pool_timeout_seconds=4.0,
        )

        async with create_http_client(config) as client:
            assert client.timeout == httpx.Timeout(connect=1.0, read=2.0, write=3.0, pool=4.0)

    async def test_user_agent_reaches_the_wire(self):
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.headers["User-Agent"])
            return httpx.Response(200)

        async with create_http_client(WorkerHttpSettings()) as client:
            client._transport = httpx.MockTransport(handler)
            await client.get("https://core.example.com/v1/bucket")

        assert seen == [get_user_agent()]

    async def test_bucket_lookup_carries_user_agent_and_no_conversation_id(self):
        """The bucket is now resolved before the target request; it identifies as the scheduler too."""
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(200, json={"bucket": "bkt"})

        creds = OboCredentials(header_name="Api-Key", header_value=SecretStr("obo-token"))
        async with create_http_client(WorkerHttpSettings()) as client:
            client._transport = httpx.MockTransport(handler)
            bucket = await DialClient(client, "https://core.example.com").get_bucket(creds)

        assert bucket == "bkt"
        assert captured[0].headers.get_list("User-Agent") == [get_user_agent()]
        assert CONVERSATION_ID_HEADER not in captured[0].headers
