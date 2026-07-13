from dataclasses import dataclass
from contextlib import contextmanager

import httpx
from openai import OpenAI

from ..config import get_settings


@dataclass
class LLMClient:
    client: OpenAI
    model: str


@contextmanager
def get_llm_client():
    """Yield an LLMClient backed by a managed httpx.Client (closed on exit)."""
    settings = get_settings()
    http_client = httpx.Client(timeout=60)
    try:
        client = OpenAI(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            http_client=http_client,
        )
        yield LLMClient(client=client, model=settings.llm_model)
    finally:
        http_client.close()
