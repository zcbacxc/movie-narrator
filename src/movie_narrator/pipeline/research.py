import json
from pathlib import Path

from ..config import get_settings
from ..models import Context, ResearchInfo, StepResult
from ..utils.json_parser import extract_json
from ..utils.llm import get_llm_client

RESEARCH_PROMPT = """\
You are a film research assistant. Provide structured information about the movie "{movie}".

Output ONLY valid JSON in this exact format:
{{
  "title": "{movie}",
  "year": 2023,
  "summary": "2-3 sentence plot summary...",
  "genres": ["Action", "Drama"],
  "cast": ["Actor 1", "Actor 2"],
  "keywords": ["keyword1", "keyword2", "keyword3"]
}}

Do NOT add any text before or after the JSON.
"""


def _write_envelope(output_dir: Path, status: str, error: str | None, research: dict | None) -> Path:
    path = output_dir / "research.json"
    payload = {
        "status": status,
        "error": error,
        "research": research or {},
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def research_plot(ctx: Context) -> Context:
    if not ctx.metadata.get("research_enabled"):
        ctx.status.research = "skipped"
        ctx.step_state.result = StepResult.SKIPPED
        ctx.step_state.message = "research disabled"
        return ctx

    provider = ctx.metadata.get("research_provider", "llm")
    output_dir = Path(ctx.output_dir)

    if provider != "llm":
        err = f"unknown provider: {provider}"
        ctx.step_state.result = StepResult.WARNING
        ctx.step_state.message = err
        _write_envelope(output_dir, "failed", err, None)
        ctx.status.research = "failed"
        return ctx

    try:
        settings = get_settings()
        with get_llm_client() as llm:
            prompt = RESEARCH_PROMPT.format(movie=ctx.movie_name)
            response = llm.client.chat.completions.create(
                model=llm.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=settings.research_temperature,
                max_tokens=settings.research_max_tokens,
            )
            raw = response.choices[0].message.content or ""
            data = extract_json(raw)

            ctx.research = ResearchInfo(
                title=data.get("title", ctx.movie_name),
                year=data.get("year"),
                summary=data.get("summary", ""),
                genres=data.get("genres", []),
                cast=data.get("cast", []),
                keywords=data.get("keywords", []),
            )
            _write_envelope(output_dir, "success", None, ctx.research.model_dump())
            ctx.status.research = "success"
            return ctx
    except Exception as e:
        err = str(e)
        ctx.step_state.result = StepResult.WARNING
        ctx.step_state.message = err
        _write_envelope(output_dir, "failed", err, None)
        ctx.status.research = "failed"
        return ctx
