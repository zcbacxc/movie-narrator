from dataclasses import dataclass

from openai import OpenAI

from ..config import get_settings


@dataclass
class LLMClient:
    client: OpenAI
    model: str


def get_llm_client() -> LLMClient:
    settings = get_settings()
    client = OpenAI(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
    )
    return LLMClient(client=client, model=settings.llm_model)
