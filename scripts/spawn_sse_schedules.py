"""Spawn N one-shot date-triggered schedules that hit the local echo SSE endpoint.

Each schedule fires once at ~`fire_in` seconds from now (default 45, must be < 60),
calling POST {base_url}/v1/echo/sse with a 30-second sleep payload.

Example:
    uv run python scripts/spawn_sse_schedules.py -n 10
    uv run python scripts/spawn_sse_schedules.py -n 5 --fire-in 50 --base-url http://localhost:5000
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime, timedelta

import httpx


async def create_schedule(
    client: httpx.AsyncClient,
    index: int,
    fire_at: datetime,
    sse_url: str,
) -> tuple[int, int, str]:
    """POST a single one-shot schedule. Returns (index, status_code, body_snippet)."""
    payload = {
        "display_name": f"sse-load-{fire_at.strftime('%H%M%S')}-{index:03d}",
        "trigger": {"date": fire_at.isoformat()},
        "properties": {
            "target_type": "sse",
            "method": "POST",
            "url": sse_url,
            "payload": {"sleep": 1, "chunks": 30, "success": True},
        },
    }
    response = await client.post("/v1/schedules/", json=payload)
    return index, response.status_code, response.text[:200]


async def main_async(args: argparse.Namespace) -> int:
    if args.fire_in >= 60:
        raise SystemExit("--fire-in must be < 60 seconds")
    if args.fire_in < 5:
        raise SystemExit("--fire-in must be >= 5 seconds (clients need time to register)")

    fire_at = datetime.now(UTC) + timedelta(seconds=args.fire_in)
    sse_url = f"{args.base_url.rstrip('/')}/echo/sse"

    headers = {"Api-Key": args.api_key}

    print(f">> Spawning {args.count} schedules firing at {fire_at.isoformat()} (in {args.fire_in}s)")
    print(f">> Target SSE URL: {sse_url}")

    async with httpx.AsyncClient(base_url=args.base_url, headers=headers, timeout=30.0) as client:
        tasks = [create_schedule(client, i, fire_at, sse_url) for i in range(args.count)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    ok = 0
    failed = 0
    for result in results:
        if isinstance(result, BaseException):
            failed += 1
            print(f"   [ERR] {result!r}")
            continue
        index, status, body = result
        if 200 <= status < 300:
            ok += 1
            print(f"   [{status}] schedule {index:03d} created")
        else:
            failed += 1
            print(f"   [{status}] schedule {index:03d} failed: {body}")

    print(f">> Done. created={ok} failed={failed}")
    return 0 if failed == 0 else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-n", "--count", type=int, required=True, help="Number of schedules to spawn.")
    parser.add_argument(
        "--fire-in",
        type=int,
        default=45,
        help="Seconds from now until each schedule fires (must be < 60). Default: 45.",
    )
    parser.add_argument(
        "--base-url",
        default="http://localhost:5000",
        help="Scheduler API base URL. Default: http://localhost:5000.",
    )
    parser.add_argument(
        "--api-key",
        required=True,
        help="DIAL API key (sent as Api-Key header).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main_async(parse_args())))
