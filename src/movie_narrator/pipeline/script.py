from ..config import get_settings
from ..models import Context, ScriptSegment
from ..utils.prompts import SCRIPT_PROMPT
from ..utils.llm import get_llm_client
from ..utils.json_parser import extract_json
from time import sleep
import os

# CI-only fallback: used when LLM is unreachable in CI environment
# to allow full pipeline testing. Never used for real users.
_CI_MOCK_SEGMENTS = [
    "{movie_name}是一部精彩的电影，",
    "讲述了令人难忘的故事。",
    "每一个场景都扣人心弦，令人回味无穷。",
    "不容错过的经典之作。",
]


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
                # LLM failed after all retries.
                # In CI: fall back to mock content (with warning) so the
                # full pipeline can be exercised without an LLM.
                # In production: hard fail — user must know the script
                # is not real, no silent fake content.
                if os.environ.get("CI"):
                    ctx.services.console.inline_warn(
                        f"LLM unreachable (CI mode): using mock script. {e}"
                    )
                    ctx.segments = [
                        ScriptSegment(text=s.format(movie_name=ctx.movie_name))
                        for s in _CI_MOCK_SEGMENTS
                    ]
                    ctx.metadata["script_source"] = "ci_mock"
                    ctx.metadata["script_degraded"] = True
                    return ctx
                raise RuntimeError(
                    f"LLM script generation failed after {settings.script_retries} attempts: {e}. "
                    f"Check your LLM configuration (MN_LLM_BASE_URL, MN_LLM_API_KEY, MN_LLM_MODEL) "
                    f"and network connectivity."
                ) from e
            sleep(settings.script_retry_delay)
    return ctx
