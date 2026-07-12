from dataclasses import dataclass

import httpx
from openai import OpenAI

from ..config import get_settings


@dataclass
class LLMClient:
    client: OpenAI
    model: str


def get_llm_client() -> LLMClient:
    settings = get_settings()
    http_client = httpx.Client(timeout=60)
    client = OpenAI(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        http_client=http_client,
    )
    return LLMClient(client=client, model=settings.llm_model)
