from aidial_sdk.chat_completion import ChatCompletion, Request, Response

from src.lib.utils.logging import logger


_CHUNKS = 3


class EchoChatCompletion(ChatCompletion):
    async def chat_completion(self, request: Request, response: Response) -> None:
        with response.create_single_choice() as choice:
            for i in range(_CHUNKS):
                choice.append_content(f"chunk {i} ")
                logger.info("echo_chat_chunk", chunk_index=i)
