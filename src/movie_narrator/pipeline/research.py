import json
from pathlib import Path

from ..config import get_settings
from ..models import Context, ResearchInfo
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
    if ctx.metadata.get("workflow_steps", {}).get("research") is False:
        ctx.status.research = "disabled"
        print("⏭ research_plot: disabled by workflow config")
        return ctx
    if not ctx.metadata.get("research_enabled"):
        ctx.status.research = "skipped"
        print("⏭ research_plot: research disabled")
        return ctx

    provider = ctx.metadata.get("research_provider", get_settings().research_provider)
    output_dir = Path(ctx.output_dir)

    if provider != "llm":
        err = f"unknown provider: {provider}"
        print(f"✗ research_plot: {err}")
        _write_envelope(output_dir, "failed", err, None)
        ctx.status.research = "failed"
        return ctx

    try:
        llm = get_llm_client()
        prompt = RESEARCH_PROMPT.format(movie=ctx.movie_name)
        response = llm.client.chat.completions.create(
            model=llm.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=1024,
        )
        raw = response.choices[0].message.content
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
        print("✓ research_plot")
        return ctx
    except Exception as e:
        err = str(e)
        print(f"✗ research_plot: {err}")
        _write_envelope(output_dir, "failed", err, None)
        ctx.status.research = "failed"
        return ctx
