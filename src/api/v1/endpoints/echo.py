import asyncio
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.lib.utils.logging import logger


router = APIRouter(prefix="/echo", tags=["echo"])


class EchoRequest(BaseModel):
    success: bool = True
    sleep: float = 0.0
    chunks: int = 3


@router.post("/rest")
async def echo_rest(request: EchoRequest) -> dict:
    if request.sleep > 0:
        await asyncio.sleep(request.sleep)
    if not request.success:
        raise HTTPException(status_code=500, detail="echo error")
    logger.info("echo_rest_ok")
    return {"ok": True}


@router.post("/sse")
async def echo_sse(request: EchoRequest) -> StreamingResponse:
    if not request.success:
        raise HTTPException(status_code=500, detail="echo error")

    async def generate() -> AsyncIterator[str]:
        if request.sleep > 0:
            await asyncio.sleep(request.sleep)
        for i in range(request.chunks):
            logger.info("echo_sse_event", chunk_index=i)
            yield f"data: chunk {i}\n\n"
            await asyncio.sleep(0)

    return StreamingResponse(generate(), media_type="text/event-stream")
