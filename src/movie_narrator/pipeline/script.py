from ..config import get_settings
from ..models import Context, ScriptSegment
from ..utils.prompts import SCRIPT_PROMPT
from ..utils.llm import get_llm_client
from ..utils.json_parser import extract_json
from time import sleep


def generate_script(ctx: Context) -> Context:
    settings = get_settings()
    for attempt in range(settings.script_retries):
        try:
            with get_llm_client() as llm:

                research_block = ""
                if ctx.research and ctx.research.summary:
                    research_block = f"\nResearch context: {ctx.research.summary}\nGenres: {', '.join(ctx.research.genres)}\n"

                prompt = SCRIPT_PROMPT.format(
                    movie=ctx.movie_name,
                    style=ctx.style,
                    duration=ctx.duration,
                    research=research_block,
                )
                response = llm.client.chat.completions.create(
                    model=llm.model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=settings.script_temperature,
                    max_tokens=settings.script_max_tokens,
                )
                raw = response.choices[0].message.content or ""
                data = extract_json(raw)
                raw_segments = data.get("segments", [])
                segments = []
                for item in raw_segments:
                    if isinstance(item, str):
                        segments.append(ScriptSegment(text=item))
                    elif isinstance(item, dict) and "text" in item:
                        segments.append(ScriptSegment(text=item["text"]))
                if not segments:
                    raise ValueError("empty script from LLM")
                ctx.segments = segments
                ctx.metadata["script_source"] = "llm"
                return ctx
        except Exception as e:
            if attempt == settings.script_retries - 1:
                # LLM failed after all retries — hard fail instead of
                # silently generating a fake "movie" script. The user
                # would get a video with meaningless placeholder text
                # and no way to tell it's not a real script.
                raise RuntimeError(
                    f"LLM script generation failed after {settings.script_retries} attempts: {e}. "
                    f"Check your LLM configuration (MN_LLM_BASE_URL, MN_LLM_API_KEY, MN_LLM_MODEL) "
                    f"and network connectivity."
                ) from e
            sleep(settings.script_retry_delay)
    return ctx
